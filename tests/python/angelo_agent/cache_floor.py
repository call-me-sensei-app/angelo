# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Prompt-cache floors: a gate run fails, as a cache SEV, when a cache read falls below its recorded floor.

A cache read costs 0.05x input on the launch models, so one silent prefix break
(a timestamp in the instructions, a tool list in another order, a cache key that
changes per process) multiplies a chat's input cost by up to 20 while every
task still passes. The gate's pass counts cannot see it; this check can.

What is measured, from the Angelo receipts a run already writes
(``eval-live-agent.json``: each task's ``receipt.usage`` and ``receipt.route``):

* A *chat* is one native session: the tasks of a trial with the same ``chat``.
* Its *first call* is the chat's first model request. Its share is the first
  turn's ``cached_input_tokens / input_tokens`` when that turn made exactly one
  request; otherwise the first call is not separable and is unknown.
* Its *calls 2+ share* is the token-weighted ``cached / input`` over every later
  turn whose ``cache_gap`` is ``under_5m`` (or unrecorded, in older receipts). A
  turn after a longer gap may have outlived the provider's shortest cache
  lifetime, so it is left out and counted. When the first turn made several
  requests it is left out too (its later requests cannot be separated).
* A *cell* is ``route mode | model | variant``; the variant is the task set
  (``plan.grading.task_set``), with the approval mode when it is not the
  default and ``new-chat-per-task`` for returning-user runs.

The check, per receipt group (one step folder) and cell that has a floor:

* the cell's calls 2+ share, token-weighted over the counted later turns of
  all its chats in the step except the one turn with the lowest share, below
  ``later_floor`` is a SEV. Providers occasionally serve one request without
  its cache (the 1.0.0 final gate's Sol trial 5 read nothing on one turn,
  under a minute after the previous one): leaving out the lowest turn absorbs
  one such request, while a prefix or key break lowers every turn;
* on a route with a cache-affinity key (``route.cache_affinity``: Codex keyed
  and team routes), the median first-call share below ``first_floor`` is a
  SEV. The first keyed chat of the cell in each step is left out: it may be the
  one that warms a key the provider had expired. Claude has no key (reuse is
  automatic and not guaranteed), so its first call is shown, never gated.

Each step cell also reports its full misses (later turns that read nothing), its
lowest chat and, when receipts carry them, the provider's miss reasons
(``receipt.cache_diagnostics`` counts, if present), for forensics; none of them
is gated by itself, and an absent one is never a failure.

A value the receipts do not give is unknown and never a SEV. A cell with no floor
is listed as unfloored, never failed: floors come only from passing gates.

Floors (``cache_floors.json``): for every cell, the lowest step value observed
across the passing gate campaigns given to ``regenerate``, minus ``MARGIN`` (5
percentage points), rounded down to a whole percent; the same rule for every
cell and both metrics. Usage::

    python3 cache_floor.py check STEP_OR_RECEIPT...          # exit 1 on a cache SEV
    python3 cache_floor.py regenerate CAMPAIGN...            # rewrite cache_floors.json from passing gates
    python3 cache_floor.py show STEP_OR_RECEIPT...           # per-chat shares, no verdict

Standard library only; doc/angelo/final_runs.rst, "Cache-read floors".
"""

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
import eval_send_record as send_record  # The trial's record: an end write, never one written at a send.

HERE = Path(__file__).resolve().parent
FLOORS_PATH = HERE / "cache_floors.json"
RECEIPT = send_record.NAME
MARGIN = 0.05
# Gaps under the shortest provider cache lifetime (5 minutes); a missing bucket is an older receipt.
COUNTED_GAPS = ("under_5m", None)
RULE = ("per cell (route mode | model | variant): the lowest step value across the passing gate campaigns in "
        "sources (calls 2+: token-weighted over the step's later turns except its lowest one; first call: median of the step's keyed chats "
        "after the first), minus %d percentage points, rounded down to a whole percent" % round(MARGIN * 100))


def receipt_paths(target):
    """The run receipts of a step folder (its own and each bench trial's) or a receipt file, in trial order."""
    target = Path(target)
    if target.is_file():
        return [target]
    own = [target / RECEIPT] if send_record.finished(target / RECEIPT) else []
    return own + send_record.finished_only(sorted(target.glob("angelo-trial-*/" + RECEIPT)))


def variant(report):
    plan = report.get("plan") or {}
    name = (plan.get("grading") or {}).get("task_set") or "unknown"
    if plan.get("approval") not in (None, "default"):
        name += "/approval-%s" % plan["approval"]
    if (plan.get("returning_user") or {}).get("new_chat_per_task"):
        name += "/new-chat-per-task"
    return name


def _share(cached, total):
    return cached / total if total else None


def _usage(task):
    receipt = task.get("receipt") or {}
    usage = receipt.get("usage") or {}
    values = [usage.get(key) for key in ("input_tokens", "cached_input_tokens")]
    if not all(type(value) is int and value >= 0 for value in values) or values[1] > values[0]:
        return receipt, None
    return receipt, usage


def chats(report, group, label=""):
    """Per chat of one receipt: its cell, whether it is keyed, its first-call share and calls 2+ token sums."""
    found = []
    for trial in report.get("trials") or []:
        if trial.get("invalid"):
            continue
        by_chat = {}
        for task in trial.get("tasks") or []:
            if task.get("chat") and task.get("receipt"):
                by_chat.setdefault(task["chat"], []).append(task)
        for chat, tasks in by_chat.items():
            first_receipt, first_usage = _usage(tasks[0])
            route = first_receipt.get("route") or {}
            model = route.get("requested_model") or ",".join(route.get("served_models") or []) or "unknown"
            mode = route.get("mode") or (report.get("connection") or {}).get("mode") or "unknown"
            first = None
            if first_usage is not None and first_usage.get("requests") == 1:
                first = _share(first_usage["cached_input_tokens"], first_usage["input_tokens"])
            cached = total = full_misses = 0
            turns, diagnostics = [], {}
            for task in tasks:
                _add_counts(diagnostics, (task.get("receipt") or {}).get("cache_diagnostics"))
            left_out = {"long_gap": 0, "unmeasured": 0, "first_turn_several_requests": 0}
            if first_usage is not None and first_usage.get("requests", 1) != 1:
                left_out["first_turn_several_requests"] = 1
            for task in tasks[1:]:
                receipt, usage = _usage(task)
                if usage is None:
                    left_out["unmeasured"] += 1
                elif (receipt.get("route") or {}).get("cache_gap") not in COUNTED_GAPS:
                    left_out["long_gap"] += 1
                else:
                    cached += usage["cached_input_tokens"]
                    total += usage["input_tokens"]
                    turns.append((usage["cached_input_tokens"], usage["input_tokens"]))
                    full_misses += usage["input_tokens"] > 0 and usage["cached_input_tokens"] == 0
            found.append({"cell": "%s|%s|%s" % (mode, model, variant(report)), "group": group,
                          "trial": label or trial.get("trial"), "chat": chat,
                          "keyed": route.get("cache_affinity") is True, "first": first,
                          "later": _share(cached, total), "later_turns": turns,
                          "full_misses": full_misses, "diagnostics": diagnostics,
                          "left_out": {key: value for key, value in left_out.items() if value}})
    return found


def measure(targets):
    """Every chat in the receipts under ``targets``; each target is one group (step) for the first-call rule."""
    found = []
    for target in targets:
        for path in receipt_paths(target):
            label = path.parent.name if path.parent.name.startswith("angelo-trial-") else ""
            found += chats(json.loads(path.read_text(encoding="utf-8")), str(target), label)
    gated = set()
    for chat in found:   # The first keyed chat of a cell in each group may warm an expired key.
        key = (chat["group"], chat["cell"])
        chat["first_gated"] = chat["keyed"] and key in gated
        if chat["keyed"]:
            gated.add(key)
    return found


def cells(found):
    """Per (group, cell): the gated statistics and their forensics.

    ``later``: token-weighted calls 2+ share over the counted later turns of all the cell's chats in the group,
    leaving out the one turn with the lowest share: a request the provider happened not to serve from cache is
    trimmed, while a prefix or key break lowers every turn.
    ``first``: the median first-call share of the gated chats (keyed routes), for the same reason.
    """
    result = {}
    for chat in found:
        entry = result.setdefault((chat["group"], chat["cell"]), {
            "group": chat["group"], "cell": chat["cell"], "chats": 0, "turns": [], "full_misses": 0, "firsts": [],
            "lowest_chat": None, "diagnostics": {}})
        entry["chats"] += 1
        entry["turns"] += chat["later_turns"]
        entry["full_misses"] += chat["full_misses"]
        _add_counts(entry["diagnostics"], chat["diagnostics"])
        if chat["first_gated"] and chat["first"] is not None:
            entry["firsts"].append(chat["first"])
        if chat["later"] is not None and (entry["lowest_chat"] is None or chat["later"] < entry["lowest_chat"][0]):
            entry["lowest_chat"] = (round(chat["later"], 4), chat["trial"], chat["chat"])
    for entry in result.values():
        turns = sorted(entry.pop("turns"), key=lambda turn: _share(*turn) if turn[1] else 1.0)
        kept = turns[1:] if len(turns) > 1 else turns   # One request's provider miss is trimmed, a break is not.
        entry["later_turns"] = len(turns)
        entry["later"] = _share(sum(turn[0] for turn in kept), sum(turn[1] for turn in kept))
        entry["first"] = statistics.median(entry["firsts"]) if entry["firsts"] else None
    return [result[key] for key in sorted(result)]


def _add_counts(totals, counts):
    """Sum a receipt's ``cache_diagnostics`` counts (the provider's miss reasons, best effort; never gated)."""
    for key, number in counts.items() if isinstance(counts, dict) else ():
        if isinstance(key, str) and type(number) is int and number > 0:
            totals[key] = totals.get(key, 0) + number


def _floor(values):
    """The rule on the lowest value as recorded (4 decimals), so a floor can be re-derived from the file."""
    return max(0.0, math.floor((round(min(values), 4) - MARGIN) * 100 + 1e-9) / 100) if values else None


def floors_from(found):
    """``cells`` for cache_floors.json: per cell the floors, the lowest and median step value, how many steps."""
    floors = {}
    measured = cells(found)
    for cell in sorted({entry["cell"] for entry in measured}):
        mine = [entry for entry in measured if entry["cell"] == cell]
        later = [entry["later"] for entry in mine if entry["later"] is not None]
        first = [entry["first"] for entry in mine if entry["first"] is not None]
        floor = {"later_floor": _floor(later), "later_steps": len(later)}
        if later:
            floor.update(later_min=round(min(later), 4), later_median=round(statistics.median(later), 4))
        if first:
            floor.update(first_floor=_floor(first), first_steps=len(first), first_min=round(min(first), 4),
                         first_median=round(statistics.median(first), 4))
        floors[cell] = floor
    return floors


def load_floors(path=FLOORS_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def check(found, floors):
    """``{"sev", "breaches", "unfloored", "cells"}``: a breach is a step's cell value below the cell's floor."""
    recorded = floors.get("cells", {})
    breaches, measured = [], cells(found)
    for entry in measured:
        floor = recorded.get(entry["cell"])
        if floor is None:
            continue
        for metric, key, label in (("later", "later_floor", "calls 2+"), ("first", "first_floor", "first call")):
            if entry[metric] is not None and floor.get(key) is not None and entry[metric] < floor[key]:
                breaches.append({"cell": entry["cell"], "group": entry["group"], "metric": label,
                                 "share": round(entry[metric], 4), "floor": floor[key],
                                 "full_misses": entry["full_misses"], "lowest_chat": entry["lowest_chat"],
                                 "diagnostics": entry["diagnostics"]})
    unfloored = sorted({entry["cell"] for entry in measured if entry["cell"] not in recorded})
    summary = [{key: (round(value, 4) if isinstance(value, float) else value) for key, value in entry.items()
                if key != "firsts"} for entry in measured]
    return {"sev": bool(breaches), "breaches": breaches, "unfloored": unfloored, "cells": summary}


def describe(result):
    """Human-readable lines; the first says SEV or clear."""
    if result["sev"]:
        lines = ["CACHE SEV: %d prompt-cache read(s) below the recorded floor (cache_floors.json); a prefix or "
                 "cache-key break multiplies input cost by up to 20 while every task passes" % len(result["breaches"])]
        lines += ["  %s: %s read %.1f%% < floor %.0f%% (%s; full misses %d; lowest chat %s; provider miss "
                  "diagnostics %s)" % (b["cell"], b["metric"], b["share"] * 100, b["floor"] * 100, b["group"],
                                       b["full_misses"], b["lowest_chat"], b["diagnostics"] or "none reported")
                  for b in result["breaches"]]
    else:
        lines = ["cache floors: clear (%d step cells checked)" % len(result["cells"])]
    lines += ["  no floor recorded for %s (not gated)" % cell for cell in result["unfloored"]]
    return lines


def check_targets(targets, floors_path=FLOORS_PATH):
    return check(measure(targets), load_floors(floors_path))


# ------------------------------------------------------------------------ regenerate

def _gate_problems(folder):
    """What kept a finished gate step from passing: failed, inconclusive, skipped or unrun tasks (the harness's own
    grading). A question with no check (``ungraded``) is not a failure; the cache read is independent of it."""
    bench = folder / "bench-mcp-vs-angelo.json"
    if bench.is_file():
        tasks = json.loads(bench.read_text(encoding="utf-8"))["summary"]["tasks"]
        gradings = [t.get("angelo", {}).get("graded") or {} for t in tasks.values()]
        gradings += [{"skipped": t.get("angelo", {}).get("skipped", 0)} for t in tasks.values()]
    else:
        gradings = [send_record.read(folder / RECEIPT, "utf-8").get("grading") or {}]
    problems = {key: sum(g.get(key, 0) for g in gradings) for key in ("failed", "inconclusive", "skipped", "not_run")}
    if not any(g.get("total") or g.get("runs") for g in gradings):
        problems["no_runs"] = 1
    return {key: value for key, value in problems.items() if value}


def gate_steps(campaign_dir):
    """A campaign's finished gate-phase step folders; refuses a campaign with a failed gate task."""
    import final_runs_plan
    campaign_dir = Path(campaign_dir)
    config = json.loads((campaign_dir / "CONFIG.json").read_text(encoding="utf-8"))
    state = json.loads((campaign_dir / "STATE.json").read_text(encoding="utf-8"))
    steps = []
    for step in final_runs_plan.campaign(config["platform"]):
        folder = campaign_dir / step["id"]
        if step["phase"] != "gate" or state["steps"].get(step["id"], {}).get("status") != "done":
            continue
        problems = _gate_problems(folder)
        if problems:
            raise SystemExit("%s: %s did not pass (%s); floors come only from passing gates" % (
                campaign_dir.name, step["id"], json.dumps(problems, sort_keys=True)))
        steps.append(folder)
    if not steps:
        raise SystemExit("%s: no finished gate step" % campaign_dir.name)
    return config, steps


def regenerate(campaign_dirs, path=FLOORS_PATH):
    """Rewrite the floors from passing gate campaigns; sources name each campaign folder and its commit only."""
    targets, sources = [], []
    for campaign_dir in campaign_dirs:
        config, steps = gate_steps(campaign_dir)
        targets += steps
        sources.append({"campaign": "%s/%s" % (Path(campaign_dir).resolve().parent.name, Path(campaign_dir).name),
                        "checkout": config.get("checkout", "")[:11], "platform": config.get("platform"),
                        "steps": [step.name for step in steps]})
    floors = {"rule": RULE, "margin": MARGIN, "sources": sources, "cells": floors_from(measure(targets))}
    Path(path).write_text(json.dumps(floors, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return floors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("action", choices=("check", "regenerate", "show"))
    parser.add_argument("paths", nargs="+", help="step folders or receipts (check, show); campaigns (regenerate)")
    parser.add_argument("--floors", default=str(FLOORS_PATH))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "regenerate":
        floors = regenerate(args.paths, args.floors)
        print(json.dumps(floors["cells"], indent=2, sort_keys=True))
        return 0
    found = measure(args.paths)
    if args.action == "show":
        print(json.dumps(found, indent=2))
        return 0
    result = check(found, load_floors(args.floors))
    print(json.dumps(result, indent=2) if args.json else "\n".join(describe(result)))
    return 1 if result["sev"] else 0


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    sys.exit(main())
