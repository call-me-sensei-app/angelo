# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Prompt-cache floors: a gate run fails, as a cache SEV, when a session's cache read falls below its recorded floor.

A cache read costs 0.05x input on the launch models, so one silent prefix break
(a timestamp in the instructions, a tool list in another order, a cache key that
changes per process) multiplies a chat's input cost by up to 20 while every
task still passes. The gate's pass counts cannot see it; this check can.

What is measured, from the Angelo receipts a run already writes
(``eval-live-agent.json``: each task's ``receipt.usage`` and ``receipt.route``):

* A *chat* is one native session: the tasks of a trial with the same ``chat``.
* Its *session share* is its cached input tokens over its input tokens, summed
  over ALL its measured calls: the first included, turns after a long gap
  included. A turn without usage is unmeasured and left out (counted).
* Its *first call* is the chat's first model request: the first turn's share
  when that turn made exactly one request; otherwise it is not separable and
  unknown.
* A *cell* is ``platform | route mode | model | variant``; the platform is the
  campaign's (``CONFIG.json``, ``unknown`` without one: never gated); the
  variant is the task set (``plan.grading.task_set``), with the approval mode
  when it is not the default and ``new-chat-per-task`` for returning-user runs.

The check (maintainer rulings 2026-11-04, "Measure over all calls" and "Skip
first turn + per-platform floors"), the same for every route and model:

* A chat's *judged share* is its session share, except for the first chat of
  its cell in each step: that one is judged without its first turn, which may
  warm a key the provider had expired (a one-chat step's chat, every smoke's,
  always is that first chat). A first chat of one turn has no judged share.
* **SEV:** a chat whose judged share is below its cell's ``session_floor``.
  A provider that serves one request without its cache costs that request's
  share of the session only (cut-15's Astra long trial 03: a cold first call,
  0.956 over 40 calls), while a prefix or key break lowers every call.
* **Warning, never a failure:** two or more first calls in one check (one run:
  a campaign's gate steps) that read less than ``FULL_READ`` of their input.
  The first chat of each cell in each step is left out: it may warm a key the
  provider had expired. One such miss is a provider's; several in a run may be
  a key or routing change worth reading.

Each cell also reports its chats, lowest chat, full misses (later turns that
read nothing) and, when receipts carry them, the provider's miss reasons
(``receipt.cache_diagnostics``), for forensics; none is gated by itself.

A value the receipts do not give is unknown and never a SEV. A cell with no floor
is listed as unfloored, never failed: floors come only from passing gates.

Floors (``cache_floors.json``): for every cell, the lowest judged share of any
chat across the passing gate campaigns given to ``regenerate``, minus ``MARGIN``
(5 percentage points), rounded down to a whole percent; one rule for every cell.
Usage::

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
# A first call that read less than this share of its input missed the cache. In the 2026-11-04 survey of 6,049
# new-session requests, full reads sit at 0.93-0.97 on Opus 5.5, Sol and Astra; partial and cold ones at 0.75 or less.
FULL_READ = 0.90
WARN_FIRST_MISSES = 2
RULE = ("per cell (platform | route mode | model | variant): the lowest judged share (cached over input tokens over a "
        "chat's measured calls; a step's first chat of the cell without its first turn) of any chat across the passing "
        "gate campaigns in sources, minus %d percentage points, rounded down to a whole percent" % round(MARGIN * 100))


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
    """Per chat of one receipt: its cell, whether it is keyed, its session share and its first-call share."""
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
            cached = total = calls = full_misses = unmeasured = rest_cached = rest_total = 0
            diagnostics = {}
            for index, task in enumerate(tasks):
                receipt, usage = _usage(task)
                _add_counts(diagnostics, receipt.get("cache_diagnostics"))
                if usage is None:
                    unmeasured += 1
                    continue
                cached += usage["cached_input_tokens"]
                total += usage["input_tokens"]
                if index > 0:
                    rest_cached += usage["cached_input_tokens"]
                    rest_total += usage["input_tokens"]
                calls += usage.get("requests") if type(usage.get("requests")) is int else 1
                full_misses += index > 0 and usage["input_tokens"] > 0 and usage["cached_input_tokens"] == 0
            found.append({"cell": "%s|%s|%s" % (mode, model, variant(report)), "group": group,
                          "trial": label or trial.get("trial"), "chat": chat,
                          "keyed": route.get("cache_affinity") is True, "first": first,
                          "session": _share(cached, total), "after_first_turn": _share(rest_cached, rest_total),
                          "input_tokens": total, "calls": calls,
                          "full_misses": full_misses, "diagnostics": diagnostics,
                          "left_out": {"unmeasured": unmeasured} if unmeasured else {}})
    return found


def platform(target):
    """The platform of the campaign a step folder or receipt belongs to (its ``CONFIG.json``), else ``unknown``."""
    for folder in list(Path(target).resolve().parents)[:4]:
        config = folder / "CONFIG.json"
        if config.is_file():
            try:
                return json.loads(config.read_text(encoding="utf-8")).get("platform") or "unknown"
            except (OSError, ValueError):
                return "unknown"
    return "unknown"


def measure(targets):
    """Every chat in the receipts under ``targets``; each target is one group (step)."""
    found = []
    for target in targets:
        where = platform(target)
        for path in receipt_paths(target):
            label = path.parent.name if path.parent.name.startswith("angelo-trial-") else ""
            for chat in chats(json.loads(path.read_text(encoding="utf-8")), str(target), label):
                chat["cell"] = where + "|" + chat["cell"]
                found.append(chat)
    seen = set()
    for chat in found:   # The first chat of a cell in each group may warm a key the provider had expired.
        key = (chat["group"], chat["cell"])
        chat["first_counted"] = key in seen
        chat["judged"] = chat["session"] if chat["first_counted"] else chat["after_first_turn"]
        seen.add(key)
    return found


def cells(found):
    """Per (group, cell): its chats, lowest judged share, first-call misses and forensics."""
    result = {}
    for chat in found:
        entry = result.setdefault((chat["group"], chat["cell"]), {
            "group": chat["group"], "cell": chat["cell"], "chats": 0, "full_misses": 0, "lowest_chat": None,
            "first_misses": [], "diagnostics": {}})
        entry["chats"] += 1
        entry["full_misses"] += chat["full_misses"]
        _add_counts(entry["diagnostics"], chat["diagnostics"])
        if chat["judged"] is not None and (entry["lowest_chat"] is None or chat["judged"] < entry["lowest_chat"][0]):
            entry["lowest_chat"] = (round(chat["judged"], 4), chat["trial"], chat["chat"])
        if chat["first_counted"] and chat["first"] is not None and chat["first"] < FULL_READ:
            entry["first_misses"].append((round(chat["first"], 4), chat["trial"], chat["chat"]))
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
    """``cells`` for cache_floors.json: per cell the floor, the lowest and median judged share, how many chats."""
    floors = {}
    for cell in sorted({chat["cell"] for chat in found}):
        sessions = [chat["judged"] for chat in found if chat["cell"] == cell and chat["judged"] is not None]
        floor = {"session_floor": _floor(sessions), "session_chats": len(sessions)}
        if sessions:
            floor.update(session_min=round(min(sessions), 4), session_median=round(statistics.median(sessions), 4))
        floors[cell] = floor
    return floors


def load_floors(path=FLOORS_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def check(found, floors):
    """``{"sev", "breaches", "warning", "first_misses", "unfloored", "cells"}``: a breach is a chat whose judged
    share is below its cell's floor; the warning is ``WARN_FIRST_MISSES`` or more first-call misses in the check."""
    recorded = floors.get("cells", {})
    breaches = []
    for chat in found:
        floor = (recorded.get(chat["cell"]) or {}).get("session_floor")
        if chat["judged"] is not None and floor is not None and chat["judged"] < floor:
            breaches.append({"cell": chat["cell"], "group": chat["group"], "trial": chat["trial"], "chat": chat["chat"],
                             "share": round(chat["judged"], 4), "session": round(chat["session"], 4),
                             "first_chat": not chat["first_counted"], "floor": floor, "calls": chat["calls"],
                             "first": None if chat["first"] is None else round(chat["first"], 4),
                             "full_misses": chat["full_misses"], "diagnostics": chat["diagnostics"]})
    measured = cells(found)
    first_misses = [dict(zip(("share", "trial", "chat"), miss), cell=entry["cell"], group=entry["group"])
                    for entry in measured for miss in entry["first_misses"]]
    unfloored = sorted({entry["cell"] for entry in measured if entry["cell"] not in recorded})
    return {"sev": bool(breaches), "breaches": breaches, "warning": len(first_misses) >= WARN_FIRST_MISSES,
            "first_misses": first_misses, "unfloored": unfloored, "cells": measured}


def describe(result):
    """Human-readable lines; the first says SEV or clear, a warning follows."""
    if result["sev"]:
        lines = ["CACHE SEV: %d session(s) read their prompt cache below the recorded floor (cache_floors.json); a "
                 "prefix or cache-key break multiplies input cost by up to 20 while every task passes"
                 % len(result["breaches"])]
        lines += ["  %s: %s chat %s read %.1f%%%s (all %d calls: %.1f%%) < floor %.0f%% (%s; first call %s; full "
                  "misses %d; provider miss diagnostics %s)" % (
                      b["cell"], b["trial"], b["chat"], b["share"] * 100, " after its first turn" if b["first_chat"] else "",
                      b["calls"], b["session"] * 100, b["floor"] * 100, b["group"], b["first"], b["full_misses"],
                      b["diagnostics"] or "none reported") for b in result["breaches"]]
    else:
        lines = ["cache floors: clear (%d step cells checked)" % len(result["cells"])]
    if result["warning"]:
        lines.append("CACHE WARNING: %d first calls read under %d%% of their input in this run (one is a provider's "
                     "miss; several may be a key or routing change): %s" % (
                         len(result["first_misses"]), round(FULL_READ * 100),
                         "; ".join("%s %s %.1f%%" % (m["cell"], m["trial"], m["share"] * 100)
                                   for m in result["first_misses"])))
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
