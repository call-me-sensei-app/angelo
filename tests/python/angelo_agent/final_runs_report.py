# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The final runs' reading side: results per step and side, subscription headroom, sign-in checks (final_runs.py).

Nothing here sends a model request: ``report`` reads the harnesses' own
summaries, ``headroom`` asks the Codex app-server for the ChatGPT usage
windows (``account/rateLimits/read``) and ``check_sign_in`` looks only at
whether the sign-in files exist, never at what they hold.
"""

import json
import os
import statistics
import sys
from pathlib import Path

import bench_totals as totals

STATE = "STATE.json"


def report(config, steps, campaign_dir):
    """Per step and side: passes, median and p75 seconds, tokens and USD per request, contention.

    A total over the step's requests is unknown (None) when a request that ran recorded no value, never a
    partial sum: ``*_recorded`` says how many requests that ran recorded one, and ``list_price_known_usd`` is
    the list price of those that did (``bench_totals``). ``cost_usd`` is on the step's one basis for all its
    sides, stated once in ``cost_basis[step]``: Claude Code's estimate when every request that ran recorded one,
    otherwise the list price.

    ``cold_warm`` gives per step each trial's launch, first request and start, the cold-start and returning-user
    medians (``bench_cold_warm``): the bench's own summary, or, for a live eval and a bench step recorded before
    it, read from the step's folder.

    Each row states its scripts (``bench_integrity.scripts_label``: "scripts allowed" for a head-to-head run with
    Always allow scripts) and its arm's planned and valid n; ``n_differs`` lists every arm whose valid n is not
    the n the campaign recorded at init (``planned_n``), and every step that has none recorded.
    """
    import bench_cold_warm
    import bench_integrity
    rows, bases, cold_warm, n_differs = [], {}, {}, []
    planned = config.get("planned_n")
    for step in steps:
        folder = Path(campaign_dir) / step["id"]
        result = folder / ("bench-mcp-vs-angelo.json" if step["tool"] == "bench" else "eval-live-agent.json")
        if not result.is_file():
            if step["tool"] != "pilot" and (planned or {}).get(step["id"]):   # The pilot keeps its own entries.
                n_differs.append("%s: not run (planned %s)" % (step["id"], ", ".join(
                    "%s %d" % item for item in sorted(planned[step["id"]].items()))))
            continue
        data = json.loads(result.read_text())
        if step["tool"] == "eval":   # The live eval's per-trial task rows; priced here as the bench prices its records.
            import bench_mcp_vs_angelo as bench
            data["records"] = [dict(row, side="angelo", wall_ms=row.get("total_ms"))
                               for trial in data.get("trials", []) for row in trial.get("tasks", [])]
            bench.price_records(data["records"], step["model"], step["runtime"])
        compared = [r for r in data.get("records", []) if r.get("side", "angelo") in step["sides"]]
        bases[step["id"]] = totals.record_basis(compared, "requests")
        cost = totals.COST_VALUES[bases[step["id"]]["basis"]]
        cold_warm[step["id"]] = ((data.get("summary") or {}).get("cold_warm")
                                 or bench_cold_warm.for_folder(folder, data.get("records", []), step["tool"],
                                                               returning=step["id"].startswith("returning-")))
        plan = data.get("plan") or (data.get("meta") or {}).get("plan") or {}
        scripts = bench_integrity.scripts_label(plan.get("angelo") if "angelo" in plan else plan)
        recorded = bench_integrity.recorded_n(data.get("records", []))
        want = (planned or {}).get(step["id"])
        n_differs += (["%s: no planned n was recorded at init" % step["id"]] if want is None else
                      ["%s %s" % (step["id"], problem) for problem in bench_integrity.n_problems(
                          want, {side: recorded.get(side, {"valid": 0}) for side in step["sides"]})])
        for side in step["sides"]:
            records = [r for r in compared if r.get("side", "angelo") == side]
            spent = totals.known_sum(records, cost)
            walls = sorted(r["wall_ms"] / 1000 for r in records if r.get("wall_ms"))
            prices = [r.get("list_price_usd") for r in totals.ran(records)]
            price = totals.known_sum(records, lambda r: r.get("list_price_usd"))
            rows.append({"step": step["id"], "side": side, "requests": len(records), "scripts": scripts,
                         "planned_n": (want or {}).get(side), "n": recorded.get(side, {}).get("valid", 0),
                         "failures": [(r.get("trial"), r.get("task"), r.get("status")) for r in records
                                      if r.get("status") != "completed"],
                         "median_s": round(statistics.median(walls), 2) if walls else None,
                         "p75_s": round(walls[int(0.75 * (len(walls) - 1))], 2) if walls else None,
                         "cost_usd": None if spent is None else round(spent, 4),
                         "cost_recorded": totals.recorded([cost(r) for r in totals.ran(records)], "requests"),
                         "list_price_usd": None if price is None else round(price, 4),
                         "list_price_known_usd": round(sum(p for p in prices if totals.is_number(p)), 4),
                         "list_price_recorded": totals.recorded(prices, "requests"),
                         "uncached_input": totals.known_sum(records, lambda r: r.get("uncached_input_tokens")),
                         "uncached_input_recorded": totals.recorded(
                             [r.get("uncached_input_tokens") for r in totals.ran(records)], "requests")})
    state_path = Path(campaign_dir) / STATE
    state = json.loads(state_path.read_text()) if state_path.is_file() else {"steps": {}}
    contended = sorted("%s %s" % (step, unit) for step, entry in state["steps"].items()
                       for unit, trial in entry.get("trials", {}).items() if (trial.get("load") or {}).get("contended"))
    returning = {step["id"]: returning_columns(Path(campaign_dir) / step["id"]) for step in steps
                 if step["id"].startswith("returning-") and (Path(campaign_dir) / step["id"]).is_dir()}
    return {"rows": rows, "cost_basis": bases, "contended_trials": contended, "returning_user": returning,
            "cold_warm": cold_warm, "n_differs": n_differs}


def headroom(config):
    """ChatGPT usage windows from the Codex profile (``account/rateLimits/read``; no model request)."""
    from angelo_agent.protocol import JsonRpcProcess
    env = {key: os.environ[key] for key in ("PATH", "USER", "LANG", "TMPDIR", "SYSTEMROOT") if key in os.environ}
    env.update(HOME=str(Path.home()), CODEX_HOME=config["codex_home"])
    rpc = JsonRpcProcess([config["codex"], "app-server", "--listen", "stdio://"], env=env,
                         on_event=lambda *a: None, on_request=lambda *a: None)
    try:
        rpc.request("initialize", {"clientInfo": {"name": "angelo_blender", "title": "Angelo", "version": "0.1.0"},
                                   "capabilities": {"experimentalApi": True}}, timeout=30)
        rpc.notify("initialized")
        limits = rpc.request("account/rateLimits/read", {}, timeout=30)
    finally:
        rpc.close()
    return {"chatgpt": limits, "claude": "No unbilled usage read exists for Claude Code; check the account's usage "
                                         "page before starting (doc/angelo/final_runs.rst)."}


def discover_connections(connections_dir):
    """``{"claude": id, "chatgpt": id}``: the one subscription connection of each kind (metadata only)."""
    from angelo_agent.connections import ConnectionStore
    found = {}
    for identity, record in ConnectionStore(Path(connections_dir)).records.items():
        account = {"claude_subscription": "claude", "codex_subscription": "chatgpt"}.get(record.get("mode"))
        if account:
            found.setdefault(account, []).append(identity)
    problems = ["%s: %d subscription connections (need exactly one)" % (account, len(found.get(account, [])))
                for account in ("claude", "chatgpt") if len(found.get(account, [])) != 1]
    if problems:
        raise SystemExit("init --discover-connections: " + "; ".join(problems))
    return {account: ids[0] for account, ids in found.items()}


# The file each CLI keeps its sign-in in, inside the connection's profile folder. Claude Code keeps it in the
# macOS Keychain there (the folder is all a file system shows) and in this file elsewhere; Codex keeps it in a file
# when its profile says so (``CODEX_CREDENTIALS_FILE``: the Windows bench's profiles do).
CLAUDE_CREDENTIALS = ".credentials.json"
CODEX_CREDENTIALS = "auth.json"
CODEX_CREDENTIALS_FILE = 'cli_auth_credentials_store = "file"\n'


def check_sign_in(connections_dir, platform=None, accounts=("claude", "chatgpt")):
    """The two subscription connections of a folder and whether each one's CLI profile is signed in (existence).

    ``accounts`` are the ones the machine's steps use (a VM that runs only ChatGPT steps needs no Claude sign-in);
    ``ready`` asks those only.
    """
    found = discover_connections(connections_dir)
    root = Path(connections_dir)
    claude = root / "claude-profile" / found["claude"]
    signed = {"claude": claude.is_dir() and ((platform or sys.platform) == "darwin"
                                             or (claude / CLAUDE_CREDENTIALS).is_file()),
              "chatgpt": (root / "codex-profile" / found["chatgpt"] / CODEX_CREDENTIALS).is_file()}
    return {"connections": found, "signed_in": signed, "needed": list(accounts),
            "ready": all(signed[account] for account in accounts)}


def prepare_connections(connections_dir):
    """A new machine's two bench connections: one Claude and one ChatGPT subscription, each with its own CLI profile
    folder in the store (never the person's own), created when absent; nothing is signed in or read.

    Returns the ids and the folders to sign in: ``claude_profile`` (Claude Code's ``CLAUDE_CONFIG_DIR``, created
    by its sign-in) and ``codex_profile`` (Codex's ``CODEX_HOME``, created here with file-kept credentials, so the
    sign-in moves with the folder instead of staying in one Windows user's credential store).
    """
    from angelo_agent.connections import ConnectionStore
    root = Path(connections_dir)
    root.mkdir(parents=True, exist_ok=True)
    store = ConnectionStore(root)
    present = {record.get("mode") for record in store.records.values()}
    for mode, provider, name in (("claude_subscription", "ANTHROPIC", "Claude (bench)"),
                                 ("codex_subscription", "OPENAI", "ChatGPT (bench)")):
        if mode not in present:
            store.add(provider, "SUBSCRIPTION", name=name, reuse_native=False)
    found = discover_connections(root)
    if any(store.records[found[account]].get("reuse_native") for account in found):
        raise SystemExit("prepare-connections: a bench connection reuses the person's own CLI sign-in; remove it")
    codex = root / "codex-profile" / found["chatgpt"]
    codex.mkdir(parents=True, exist_ok=True)
    config = codex / "config.toml"
    if not config.exists():
        config.write_text(CODEX_CREDENTIALS_FILE, encoding="utf-8")
    elif CODEX_CREDENTIALS_FILE.strip() not in config.read_text(encoding="utf-8"):
        raise SystemExit("prepare-connections: %s exists without %s" % (config, CODEX_CREDENTIALS_FILE.strip()))
    return {"connections": found, "claude_profile": str(root / "claude-profile" / found["claude"]),
            "codex_profile": str(codex)}


def chatgpt_window(config):
    """``(used percent, resets at)`` of the ChatGPT usage window now, or None when it cannot be read (unbilled)."""
    if not config.get("codex") or not config.get("codex_home"):
        return None
    try:
        primary = headroom(config)["chatgpt"]["rateLimits"]["primary"]
        return float(primary["usedPercent"]), float(primary["resetsAt"])
    except Exception:   # Best effort: a read failure never stops the campaign; the harness's limit check still does.
        return None


POSITIONS = ("cold start", "relaunch, first chat", "new chat, running app")


def _position(launch, index):
    return POSITIONS[0] if launch == 1 and index == 0 else POSITIONS[1] if index == 0 else POSITIONS[2]


def returning_columns(folder):
    """A ``returning-*`` step's cold-start and returning-user columns, per side and position: tasks, first
    requests' cached share (median), how Angelo's chats began (fork of a warm base or a fresh start), and the
    background warm-ups' own requests (from ``eval_request_log``; the MCP side records each task's first request)."""
    import eval_request_log
    folder, rows = Path(folder), {"angelo": [], "mcp": []}
    warm_ups = []
    for log in sorted(folder.glob("angelo-trial-*/trial-01/qa/requests.jsonl")):
        launch = int(log.parents[2].name.rsplit("-", 1)[1])
        threads = eval_request_log.summary(log)
        warm_ups += [{"launch": launch, "requests": t["requests"]} for t in threads if t["warm_up"]]
        chats = [t for t in threads if not t["warm_up"] and t["began"] in ("start", "fork") and t["requests"]]
        rows["angelo"] += [{"position": _position(launch, index), "began": t["began"],
                            "share": t["first_cached_share"]} for index, t in enumerate(chats)]
    for path in sorted(folder.glob("mcp-trial-*/trial.json")):
        launch = int(path.parent.name.rsplit("-", 1)[1])
        for index, task in enumerate(json.loads(path.read_text()).get("tasks", [])):
            first = task.get("first_request") or {}
            share = round(first["cached"] / first["input"], 3) if first.get("input") else None
            rows["mcp"].append({"position": _position(launch, index), "began": "thread", "share": share})
    columns = {}
    for side, entries in rows.items():
        for position in POSITIONS:
            chosen = [e for e in entries if e["position"] == position]
            shares = [e["share"] for e in chosen if e["share"] is not None]
            columns["%s/%s" % (side, position)] = {
                "tasks": len(chosen), "forked": sum(e["began"] == "fork" for e in chosen),
                "first_request_cached_share_median": statistics.median(shares) if shares else None}
    return {"columns": columns, "warm_ups": warm_ups}

