# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Step extraction, statistics and the Markdown summary of ``bench_mcp_vs_angelo.py``.

Dependency-free and deterministic: it reads only already-written records, so a
summary can be rebuilt (or a parsing fix applied) without sending any message.

Units. *Wall time* is prompt sent to final result: on the MCP side the arrival
of Claude Code's ``result`` line (the first prompt's clock starts at process
spawn); on the Angelo side the controller receipt's ``total_ms`` (the first
message includes its runtime launch). *Model API calls* are distinct assistant
message ids in Claude Code's own output (MCP: its stdout stream; Angelo: its
session transcript), so both sides count the same unit. Usage uses
``provider_usage`` on both sides. Timing statistics include runs whose host
checks failed; pass rates count skipped and failed runs as failures.

Totals (``bench_totals``). A trial's total is unknown when a task that ran
did not record the value, never a partial sum, and the summary states how
many trials a median covers. Every cost of the report is on one basis for
both sides (``summary["cost_basis"]``, stated once): Claude Code's own
estimate (``cost_usd``) when every head-to-head task that ran on either side
recorded one, otherwise the list price (``list_price_usd``; Codex records no
estimate).

Grading. ``passed`` and the pass rates are ``host_check_passed is True``;
each pass cell and rate shows beside it the runs the grading contract
(``eval_grading``) counts as failed and as inconclusive (only undecided checks
unmet; never a pass), and ``graded`` states each task's and each side's runs
as passed, failed, inconclusive, ungraded and skipped. From gate set
version 2 (``eval_gate``) the two agree, because a question passes only on its
answer. For version 1 a question's ``passed`` is the unchanged scene alone, so
the summary marks those cells and the Graded row counts them as ungraded; the
recorded figure is never read as "every answer was right". The summary states
the version, and says so when its sides were recorded with different ones
(``bench_mcp_gate``, which also prints finished runs' totals).

Cold start and returning user. Each trial is classified by its launch
position, never by a cache hit (``bench_cold_warm``): the first task after a
fresh launch is a cold start, with its launch's warm-ups and any opening wait
in it; a relaunch on a kept profile is a returning user, reported apart.
"""

import json
from pathlib import Path

import bench_cold_warm as cold_warm
import bench_integrity as integrity
import bench_mcp_gate as gate
import bench_totals as totals
import eval_answers as answers
import eval_grading as grading
import eval_live_tasks as live

HEAD_TO_HEAD = ("read_only_question", "cube_creation", "transform", "bevel_and_shade", "material")
ANGELO_ONLY = ("preview_accept",)
# Host recoveries of a program whose bound frame moved (angelo_agent.program_context).
RECOVERY_COUNTS = ("frame_rebinds", "staging_restarts", "playback_waits")
SIDES = ("mcp", "angelo")
LABELS = {"mcp": "Blender MCP + Claude Code", "angelo": "Angelo (native)"}
INIT_FIELDS = ("model", "claude_code_version", "permissionMode", "apiKeySource", "mcp_servers", "tools",
               "output_style", "fast_mode_state", "cwd")
USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens",
                "api_ms", "cost_usd")
# The configured tasks by id: the grading contract needs each task to know whether a check reads its answer.
TASKS = {task["id"]: task for task in live.build_tasks()}


def use_tasks(task_list):
    """Report over a configured task list (``eval_live_tasks.tasks_for``); previews are Angelo-only."""
    global HEAD_TO_HEAD, ANGELO_ONLY, TASKS
    HEAD_TO_HEAD = tuple(task["id"] for task in task_list if task["kind"] != "preview")
    ANGELO_ONLY = tuple(task["id"] for task in task_list if task["kind"] == "preview")
    TASKS = {task["id"]: task for task in task_list}


def _answer(task):
    """What a benchmark record keeps of a question's answer check: its outcome and how the line was read."""
    answer = answers.answer_of(task)
    kept = ("form", "outcome", "answered", "stated", "parsed", "lenient")
    return {key: answer[key] for key in kept if key in answer} or None


def session_curve(records, marks=(5, 10, 20, 30, 40, 60, 80, 100, 120)):
    """Long sessions: per side, median cumulative cost and last call's input by task position.

    Only trials that ran every head-to-head task in order are counted. A trial is unknown at a mark when a task
    up to it ran without its cost or API calls recorded; costs are on the report's one basis (``cost_basis``).
    """
    curve, cost = {}, totals.COST_VALUES[cost_basis(records)["basis"]]
    for side in SIDES:
        trials = {}
        for record in records:
            if record["side"] == side and record["task"] in HEAD_TO_HEAD:
                trials.setdefault(record["trial"], {})[record["task"]] = record
        complete = [[rows[task] for task in HEAD_TO_HEAD] for rows in trials.values() if len(rows) == len(HEAD_TO_HEAD)]
        for mark in (m for m in marks if m <= len(HEAD_TO_HEAD)):
            costs = [totals.known_sum(rows[:mark], cost) for rows in complete]
            calls = [totals.known_sum(rows[:mark], lambda r: r.get("api_calls")) for rows in complete]
            curve.setdefault(side, {})[mark] = {
                "cost_usd": describe(costs), "api_calls": describe(calls),
                "input_tokens_per_call": describe([totals.input_per_call(rows[mark - 1]) for rows in complete])}
    return curve


def _ms(stamp, start):
    return round((stamp - start) * 1000, 3)


def _addon_error(content):
    """The MCP add-on reports a failed script as ``{"status": "error"}`` inside a successful tool result."""
    texts = [content] if isinstance(content, str) else [b.get("text", "") for b in content or [] if isinstance(b, dict)]
    for text in texts:
        try:
            if json.loads(text).get("status") == "error":
                return True
        except (ValueError, AttributeError):
            continue
    return False


def script_call(block):
    """Whether a tool_use block is Blender MCP's scene-changing call (its Python), the counterpart of the first
    plan or script row on Angelo's side (``eval_receipts.ProgressWatch``, ``first_plan_row_ms``): timed from the
    call's start, the earliest moment a client could draw it."""
    return block.get("type") == "tool_use" and str(block.get("name", "")).split("__")[-1].startswith(
        "execute_blender_code")


def analyze_mcp_events(events, start):
    """Timings and steps of one Claude Code turn from timestamped stream-json messages."""
    first_text = first_tool = first_script = init = None
    api_ids, tools, retries = [], {}, []
    for stamp, message in events:
        kind = message.get("type")
        if kind == "system" and message.get("subtype") == "init":
            init = {key: message[key] for key in INIT_FIELDS if key in message}
            init["received_ms"] = _ms(stamp, start)
        elif kind == "system" and message.get("subtype") == "api_retry":
            retries.append({"at_ms": _ms(stamp, start), "status": message.get("error_status"),
                            "error": message.get("error"), "delay_ms": message.get("retry_delay_ms")})
        elif kind == "stream_event":
            event = message.get("event") or {}
            if (first_text is None and event.get("type") == "content_block_delta"
                    and (event.get("delta") or {}).get("type") == "text_delta"):
                first_text = _ms(stamp, start)
            if (first_tool is None and event.get("type") == "content_block_start"
                    and (event.get("content_block") or {}).get("type") == "tool_use"):
                first_tool = _ms(stamp, start)
            if first_script is None and event.get("type") == "content_block_start" and script_call(
                    event.get("content_block") or {}):
                first_script = _ms(stamp, start)
        elif kind == "assistant":
            body = message.get("message") or {}
            if body.get("id") and body["id"] not in api_ids:
                api_ids.append(body["id"])
            for block in body.get("content") or []:
                if block.get("type") == "text" and block.get("text") and first_text is None:
                    first_text = _ms(stamp, start)
                elif block.get("type") == "tool_use":
                    first_tool = _ms(stamp, start) if first_tool is None else first_tool
                    first_script = _ms(stamp, start) if first_script is None and script_call(block) else first_script
                    tools.setdefault(block.get("id"), {"name": block.get("name"), "issued_ms": _ms(stamp, start),
                                                       "input_chars": len(json.dumps(block.get("input")))})
        elif kind == "user":
            content = (message.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") in tools:
                    entry = tools[block["tool_use_id"]]
                    entry["returned_ms"] = _ms(stamp, start)
                    entry["round_trip_ms"] = round(entry["returned_ms"] - entry["issued_ms"], 3)
                    entry["is_error"] = bool(block.get("is_error")) or _addon_error(block.get("content"))
    calls = list(tools.values())
    return {"init": init, "first_text_ms": first_text, "first_tool_call_ms": first_tool,
            "first_script_call_ms": first_script, "api_calls": len(api_ids),
            "tool_calls": calls, "tool_call_count": len(calls), "tool_names": [call["name"] for call in calls],
            "tool_errors": sum(bool(call.get("is_error")) for call in calls), "api_retries": retries,
            "tool_round_trip_ms_total": round(sum(call.get("round_trip_ms", 0) for call in calls), 3)}


def result_summary(result):
    if not result:
        return None
    keys = ("subtype", "is_error", "num_turns", "duration_ms", "duration_api_ms", "total_cost_usd", "stop_reason")
    summary = {key: result.get(key) for key in keys if key in result}
    summary.update(models=sorted(result.get("modelUsage") or {}), usage=result.get("usage"),
                   permission_denials=result.get("permission_denials") or [],
                   reply=str(result.get("result") or "")[:1500])
    return summary


def _user_text(entry):
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def angelo_transcript_steps(path, tasks):
    """Per task id: model API calls and the native tools each typed plan requested.

    Tasks are matched in order: the long set repeats one question prompt, and
    matching any task with that prompt credited every repeat to a single id,
    so the other repeats reported no API calls at all.
    """
    ordered = [(task["id"], task["prompt"]) for task in tasks]
    steps, current, position = {}, None, 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") == "user":
            text = _user_text(entry)
            match = next((index for index in range(position, len(ordered)) if text.startswith(ordered[index][1])), None)
            if match is not None:
                current, position = ordered[match][0], match + 1
                steps[current] = {"api_ids": [], "plans": [], "models": []}
        elif entry.get("type") == "assistant" and current:
            body, record = entry.get("message") or {}, steps[current]
            if body.get("id") and body["id"] not in record["api_ids"]:
                record["api_ids"].append(body["id"])
                record["models"].append(body.get("model"))
            for block in body.get("content") or []:
                if block.get("type") == "tool_use" and block.get("name") == "StructuredOutput":
                    operations = (block.get("input") or {}).get("operations") or []
                    record["plans"].append([op.get("tool") for op in operations if isinstance(op, dict)])
    return {task: {"api_calls": len(r["api_ids"]), "plans": r["plans"],
                   "models": sorted(set(filter(None, r["models"]))),
                   "tool_names": [name for plan in r["plans"] for name in plan]} for task, r in steps.items()}


def angelo_records(trial_report, trial, steps):
    """Normalize one Angelo harness trial (its own receipts) into benchmark records."""
    records = []
    for index, task in enumerate(trial_report.get("tasks", [])):
        receipt = task.get("receipt") or {}
        counts, step = receipt.get("counts") or {}, steps.get(task["task"]) or {}
        records.append({
            "side": "angelo", "trial": trial, "task": task["task"], "kind": task["kind"], "status": task["status"],
            "runtime_start": task.get("runtime_start"), "wall_ms": task.get("total_ms"),
            "send_to_idle_ms": task.get("send_to_idle_ms"), "first_text_ms": task.get("first_text_ms"),
            "first_progress_ms": task.get("first_progress_ms"),
            "first_verified_result_ms": task.get("first_verified_result_ms"),
            "model_rounds": task.get("model_rounds"), "provider_turn_requests": task.get("provider_turn_requests"),
            "native_tool_invocations": counts.get("tool_invocations"), "python_fallbacks": task.get("python_fallbacks"),
            "api_calls": step.get("api_calls"), "tool_names": step.get("tool_names"), "plans": step.get("plans"),
            "models": step.get("models"), "tool_call_count": len(step["tool_names"]) if "tool_names" in step else None,
            "approved": task.get("approved"), "declined": task.get("declined"), "grade": task.get("grade"),
            "host_check": task.get("host_check"),   # As the MCP side's: whether a fail was only undecided.
            "gate_version": (trial_report.get("grading") or {}).get("gate_version"), "answer": _answer(task),
            # The chat's approval mode (eval_approval); none: recorded in Ask, before the default changed.
            "approval": trial_report.get("approval"), "always_allow_scripts": trial_report.get("always_allow_scripts"),
            "grading_contract": (trial_report.get("grading") or {}).get("contract"),
            "provider_usage": task.get("provider_usage"), "host_check_passed": task.get("host_check_passed"),
            # The native session's prompt-cache TTL (Controller.session_cache_ttl), which prices its unsplit writes.
            "cache_ttl": (receipt.get("route") or {}).get("cache_ttl"),
            "warm_ups": task.get("warm_ups"), "warm_up_usage": task.get("warm_up_usage"),
            "warm_up_ms": task.get("warm_up_ms"), "prompt_sha256": task.get("prompt_sha256"),
            "test_code": task.get("test_code"),   # What the trial was sent and graded with (bench_integrity).
            # Whether anything was readied before this trial's first prompt (eval_receipts.opening_wait).
            "opening": trial_report.get("opening") if index == 0 else None,
            "environment": task.get("environment"), "harness_reset": task.get("harness_reset"),
            "receipt_counts": {key: value for key, value in counts.items()
                               if key.startswith("failure_") or key in RECOVERY_COUNTS} or None,
            "accept": {k: v for k, v in (task.get("accept") or {}).items() if k != "receipt"} or None})
    return records


def describe(values):
    values = [v for v in values if type(v) in {int, float}]
    if not values:
        return {"n": 0}
    return {"n": len(values), "median": round(live.percentile(values, .5), 3),
            "p75": round(live.percentile(values, .75), 3), "min": round(min(values), 3), "max": round(max(values), 3)}


def cost_basis(records):
    """The report's one cost basis for both sides, from the head-to-head tasks that ran (``bench_totals``)."""
    return totals.record_basis([r for r in records if r["task"] in HEAD_TO_HEAD], "head-to-head tasks")


def summarize(records):
    """Per side and task, plus per-trial totals over the head-to-head tasks."""
    summary = {"tasks": {}, "totals": {}, "cost_basis": cost_basis(records)}
    cost = totals.COST_VALUES[summary["cost_basis"]["basis"]]
    if gate.is_gate_list(TASKS.values()):
        summary["gate_versions"] = gate.side_versions(records)
    for side in SIDES:
        rows = [r for r in records if r["side"] == side]
        unchecked = [task for task in HEAD_TO_HEAD + ANGELO_ONLY
                     if gate.unchecked(TASKS.get(task), summary.get("gate_versions", {}).get(side, 2))]
        for task in HEAD_TO_HEAD + ANGELO_ONLY:
            runs = [r for r in rows if r["task"] == task]
            if not runs:
                continue
            attempted = [r for r in runs if r["status"] != "skipped"]
            passed = [r for r in attempted if r.get("host_check_passed") is True]
            usage = [r.get("provider_usage") or {} for r in attempted]
            per_call = [totals.input_per_call(r) for r in attempted]
            summary["tasks"].setdefault(task, {})[side] = {
                "runs": len(runs), "skipped": len(runs) - len(attempted),
                "passed": sum(r.get("host_check_passed") is True for r in runs),
                "completed": sum(r["status"] == "completed" for r in runs),
                "pass_rate": round(sum(r.get("host_check_passed") is True for r in runs) / len(runs), 3),
                "graded": grading.totals(runs, TASKS.values()), "answer_unchecked": task in unchecked,
                "wall_ms": describe([r.get("wall_ms") for r in attempted]),
                "wall_ms_passed_only": describe([r.get("wall_ms") for r in passed]),
                "first_text_ms": describe([r.get("first_text_ms") for r in attempted]),
                "first_feedback_ms": describe([first_feedback_ms(r) for r in attempted]),
                "first_plan_row_ms": describe([first_plan_row_ms(r) for r in attempted]),
                "input_tokens_per_call": describe(per_call),
                "api_calls": describe([r.get("api_calls") for r in attempted]),
                "tool_calls": describe([r.get("tool_call_count") for r in attempted]),
                "list_price_usd": describe([r.get("list_price_usd") for r in attempted]),
                **{field: describe([u.get(field) for u in usage]) for field in USAGE_FIELDS}}
        per_trial = {}
        for record in rows:
            if record["task"] in HEAD_TO_HEAD:
                per_trial.setdefault(record["trial"], []).append(record)
        complete = {t: rs for t, rs in per_trial.items() if len(rs) == len(HEAD_TO_HEAD)
                    and all(type(r.get("wall_ms")) in {int, float} for r in rs)}
        head = [r for r in rows if r["task"] in HEAD_TO_HEAD]
        calls = [totals.known_sum(rs, lambda r: r.get("api_calls")) for rs in complete.values()]
        costs = [totals.known_sum(rs, cost) for rs in complete.values()]
        summary["totals"][side] = {
            "trials": len(per_trial), "trials_with_all_five_timed": len(complete),
            "trials_all_five_passed": sum(all(r.get("host_check_passed") is True for r in rs)
                                          for rs in per_trial.values()),
            "wall_ms": describe([sum(r["wall_ms"] for r in rs) for rs in complete.values()]),
            "api_calls": describe(calls), "api_calls_recorded": totals.recorded(calls, "trials"),
            "cost_usd": describe(costs), "cost_usd_recorded": totals.recorded(costs, "trials"),
            "head_to_head_pass_rate": round(sum(r.get("host_check_passed") is True for r in head)
                                            / max(1, len(head)), 3),
            "graded": grading.totals(head, TASKS.values(), planned=len(per_trial) * len(HEAD_TO_HEAD)),
            "answers": answers.lenient_counts(head),
            "answer_unchecked_tasks": [task for task in HEAD_TO_HEAD if task in unchecked]}
    summary["cold_warm"] = cold_warm.summarize(records, HEAD_TO_HEAD)
    return summary


def _addon_round_trip_ms(record):
    return sum(call.get("round_trip_ms", 0) for call in record.get("tool_calls") or []
               if str(call.get("name")).startswith("mcp__"))


def first_plan_row_ms(record):
    """First row saying what the turn will do: MCP's first Python call (``script_call``, from its start); Angelo's
    first provisional step, script line or plan or edit call row (``eval_receipts.ProgressWatch``)."""
    value = record.get("first_script_call_ms" if record["side"] == "mcp" else "first_plan_row_ms")
    return value if type(value) in {int, float} else None


def first_feedback_ms(record):
    """First thing the user sees: MCP's first streamed text or tool call; Angelo's first reply text or live-row
    progress (a plan's provisional steps, its names or a reasoning title; eval_receipts.ProgressWatch)."""
    values = [record.get("first_text_ms")] + ([record.get("first_tool_call_ms")] if record["side"] == "mcp"
                                              else [record.get("first_progress_ms")])
    values = [v for v in values if type(v) in {int, float}]
    return min(values) if values else None


def observations(records):
    """Run facts that bear on interpretation, computed from the records."""
    mcp = sorted((r for r in records if r["side"] == "mcp"), key=lambda r: (r["trial"], r["task"]))
    angelo = [r for r in records if r["side"] == "angelo" and r["task"] in HEAD_TO_HEAD]
    trials = sorted({r["trial"] for r in records})
    # Per trial over the tasks that ran; unknown when one of them did not record the value (bench_totals).
    by_trial = lambda rows, value: [totals.known_sum([r for r in rows if r["trial"] == t], value) for t in trials]
    share = [totals.share(rows, lambda r: (r.get("provider_usage") or {}).get("api_ms"), lambda r: r.get("wall_ms"))
             for rows in ([r for r in mcp if r["task"] in HEAD_TO_HEAD], angelo)]
    return [
        "Model API time as a share of head-to-head wall time: MCP %s, Angelo %s." % tuple(share),
        "Model API calls per trial (%d tasks): MCP %s; Angelo %s." % (len(HEAD_TO_HEAD), totals.listed(
            by_trial([r for r in mcp if r["task"] in HEAD_TO_HEAD], lambda r: r.get("api_calls"))),
            totals.listed(by_trial(angelo, lambda r: r.get("api_calls")))),
        "Wall s per trial (%d tasks): MCP %s; Angelo %s." % (len(HEAD_TO_HEAD),
            totals.listed(by_trial(mcp, lambda r: r.get("wall_ms")), .001, 1),
            totals.listed(by_trial(angelo, lambda r: r.get("wall_ms")), .001, 1)),
        "MCP add-on socket round trips per trial, s (includes its 0.25/1 s polling): %s." % totals.listed(
            by_trial(mcp, _addon_round_trip_ms), .001, 2),
        "Angelo opening before the first prompt, per trial: %s." % ([
            "readied, waited %s s" % (r["opening"].get("waited_s")) if r["opening"].get("prepared")
            else "none, runtime already running" if r["opening"].get("runtime_live") else "none"
            for r in sorted((r for r in records if r["side"] == "angelo" and r.get("opening")),
                            key=lambda r: r["trial"])] or "not recorded"),
        "MCP cold start (process spawn to Claude Code init), ms: %s." % [
            round(r["init"]["received_ms"]) for r in mcp if r.get("runtime_start") == "cold" and r.get("init")],
        "MCP first-message prompt-cache writes per trial (tokens): %s." % [
            (r.get("provider_usage") or {}).get("cache_write_input_tokens")
            for r in mcp if r.get("runtime_start") == "cold"],
        "MCP permission denials %d, API retries %d, add-on script errors %d; "
        "Angelo declined tools %d, Python fallbacks %d." % (
            sum(len((r.get("result") or {}).get("permission_denials") or []) for r in mcp),
            sum(len(r.get("api_retries") or []) for r in mcp), sum(r.get("tool_errors") or 0 for r in mcp),
            sum(len(r.get("declined") or []) for r in records if r["side"] == "angelo"),
            sum(r.get("python_fallbacks") or 0 for r in records if r["side"] == "angelo"))]


def tool_usage(records):
    """How often each tool was called, per side and task (where the steps went)."""
    usage = {}
    for record in records:
        counts = usage.setdefault(record["side"], {}).setdefault(record["task"], {})
        for name in record.get("tool_names") or []:
            counts[name] = counts.get(name, 0) + 1
    return usage


def _s(stat, key="median", scale=.001, digits=1):
    value = (stat or {}).get(key)
    return "–" if value is None else ("%.*f" % (digits, value * scale))


def _invalid_lines(invalid):
    """The visible count of rate-limited trials, which no pass rate below includes."""
    if not invalid:
        return []
    counts = "; ".join("%s %d trial(s), %d task(s)" % (side, entry["trials"], entry["tasks"])
                       for side, entry in sorted(invalid.items()))
    return ["**Invalid (rate-limited), excluded from every pass rate and median: %s.** The provider's rate or "
            "usage limit was reached; those results say nothing about the code. Rerun them." % counts, ""]


def markdown(report):
    summary, meta = report["summary"], report["meta"]
    contracts = {grading.record_contract(record) for record in report.get("records", [])}
    contract_lines = ([grading.contract_notice(contracts) or "Grading contract: %s." % grading.contract_label(contracts),
                       ""] if contracts else [])
    lines = ["# Blender MCP vs Angelo: latency and correctness", "",
             "Generated by `tests/python/angelo_agent/bench_mcp_vs_angelo.py` on %s. "
             "n = %s trials per side, alternating." % (meta.get("finished_at"), meta.get("trials")), "",
             *integrity.markdown_lines(meta.get("conditions")),   # Scripts and planned n (bench_integrity).
             *gate.version_lines(summary.get("gate_versions")),
             *contract_lines,
             *_invalid_lines(summary.get("invalid_rate_limited")),
             "## Head-to-head (%d tasks, same prompts, default-cube start)" % len(HEAD_TO_HEAD), "",
             "Wall seconds, median (p75) [min–max]; every attempted run, failed checks included.", "",
             "| Task | MCP wall s | Angelo wall s | MCP passed/runs | Angelo passed/runs | MCP API calls "
             "| Angelo API calls | MCP tools | Angelo tools |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for task in HEAD_TO_HEAD:
        row = summary["tasks"].get(task, {})
        m, a = row.get("mcp", {}), row.get("angelo", {})
        cell = lambda s: "%s (%s) [%s–%s]" % (_s(s), _s(s, "p75"), _s(s, "min"), _s(s, "max"))
        passes = lambda x: grading.pass_cell(x.get("passed", 0), x.get("runs", 0), x.get("graded")) + (
            "\\*" if x.get("answer_unchecked") else "")
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            task, cell(m.get("wall_ms")), cell(a.get("wall_ms")), passes(m), passes(a),
            _s(m.get("api_calls"), scale=1, digits=0),
            _s(a.get("api_calls"), scale=1, digits=0), _s(m.get("tool_calls"), scale=1, digits=0),
            _s(a.get("tool_calls"), scale=1, digits=0)))
    t = summary["totals"]
    lines += ["", "| Per-trial total of the %d tasks | MCP | Angelo |" % len(HEAD_TO_HEAD), "| --- | --- | --- |"]
    for label, key, scale, digits in (("Wall s, median (p75)", "wall_ms", .001, 1),
                                       ("Model API calls, median", "api_calls", 1, 0),
                                       ("Cost USD, median (basis below)", "cost_usd", 1, 3)):
        cells = ("%s (%s)%s" % (_s(t.get(s, {}).get(key), scale=scale, digits=digits),
                                _s(t.get(s, {}).get(key), "p75", scale, digits), totals.covers(t.get(s, {}), key))
                 for s in SIDES)
        lines.append("| %s | %s | %s |" % (label, *cells))
    lines.append("| Head-to-head pass rate, all runs (inconclusive runs) | %s | %s |" % tuple("%s (%d)" % (
        t.get(s, {}).get("head_to_head_pass_rate"), t.get(s, {}).get("graded", {}).get("inconclusive", 0))
        for s in SIDES))
    lines.append("| Trials with all %d passed | %s/%s | %s/%s |" % (
        len(HEAD_TO_HEAD), t.get("mcp", {}).get("trials_all_five_passed"), t.get("mcp", {}).get("trials"),
        t.get("angelo", {}).get("trials_all_five_passed"), t.get("angelo", {}).get("trials")))
    lines.append("| Graded (a run is passed only when a check read its result) | %s | %s |" % tuple(
        grading.describe(t[s]["graded"]) if t.get(s, {}).get("graded", {}).get("total") else "–" for s in SIDES))
    if any(t.get(s, {}).get("answers", {}).get("read") for s in SIDES):
        lines.append("| Answers read, of them parsed leniently | %s | %s |" % tuple(
            "%d, %d" % (t[s]["answers"]["read"], t[s]["answers"]["parsed_leniently"])
            if t.get(s, {}).get("answers", {}).get("read") else "–" for s in SIDES))
    lines += ["", "Cost basis, the same on both sides for every cost in this report: %s." % (
        summary.get("cost_basis") or {}).get("label", "not recorded")]
    if any(t.get(s, {}).get("answer_unchecked_tasks") for s in SIDES):
        lines += ["", gate.UNCHECKED_NOTE]
    lines += cold_warm.markdown_lines(summary.get("cold_warm"))   # Absent from a summary made before it.
    curve = report.get("session_curve")
    if curve and len(HEAD_TO_HEAD) > 5:
        lines += ["", "## Over the session (medians of complete trials)", "",
                  "| After task | MCP cumulative USD | Angelo cumulative USD | MCP calls | Angelo calls "
                  "| MCP input/call | Angelo input/call |", "| --- | --- | --- | --- | --- | --- | --- |"]
        for mark in sorted(set(curve.get("mcp", {})) | set(curve.get("angelo", {}))):
            m, a = curve.get("mcp", {}).get(mark, {}), curve.get("angelo", {}).get(mark, {})
            lines.append("| %d | %s | %s | %s | %s | %s | %s |" % (
                mark, _s(m.get("cost_usd"), scale=1, digits=3), _s(a.get("cost_usd"), scale=1, digits=3),
                _s(m.get("api_calls"), scale=1, digits=0), _s(a.get("api_calls"), scale=1, digits=0),
                _s(m.get("input_tokens_per_call"), scale=1, digits=0),
                _s(a.get("input_tokens_per_call"), scale=1, digits=0)))
    lines += ["", "## Where the time went (medians per task)", "",
              "| Task | Side | Model API calls | Input tokens per call | Model API s | Output tokens | Wall s "
              "| First feedback s |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for task in HEAD_TO_HEAD:
        for side in SIDES:
            stats = summary["tasks"].get(task, {}).get(side, {})
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                task, side, _s(stats.get("api_calls"), scale=1, digits=0),
                _s(stats.get("input_tokens_per_call"), scale=1, digits=0),
                _s(stats.get("api_ms")), _s(stats.get("output_tokens"), scale=1, digits=0), _s(stats.get("wall_ms")),
                _s(stats.get("first_feedback_ms"))))
    lines += ["", "First feedback: MCP = first streamed text or tool call; Angelo = first reply text of its plan.", "",
              "## Observations from this run", ""] + ["- %s" % item for item in report.get("observations", [])]
    lines += ["", "## Tokens per task (median, all attempted runs)", "",
              "| Task | Side | Input (incl. cache) | Cached read | Cache write | Output | API s "
              "| Cost USD |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    priced = "cost_usd" if (summary.get("cost_basis") or {}).get("basis") == "reported" else "list_price_usd"
    for task in HEAD_TO_HEAD + ANGELO_ONLY:
        for side, stats in summary["tasks"].get(task, {}).items():
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                task, side, *(_s(stats.get(f), scale=1, digits=0) for f in USAGE_FIELDS[:4]),
                _s(stats.get("api_ms")), _s(stats.get(priced), scale=1, digits=4)))
    preview = summary["tasks"].get("preview_accept", {}).get("angelo")
    if preview:
        lines += ["", "## Angelo-only: staged preview then accept (not in the head-to-head)", "",
                  "Wall s median (p75): %s (%s); passed %s/%s. Blender MCP has no staged preview/accept."
                  % (_s(preview["wall_ms"]), _s(preview["wall_ms"], "p75"), preview["passed"], preview["runs"])]
    lines += ["", "## Where the steps went (tool calls summed over all trials)", ""]
    for side, tasks in report.get("tool_usage", {}).items():
        for task, counts in tasks.items():
            if counts:
                lines.append("- %s / %s: %s" % (side, task, ", ".join("%s ×%d" % item for item in
                                                                        sorted(counts.items(), key=lambda i: -i[1]))))
    lines += ["", "## Method and environment", ""] + ["- %s" % item for item in meta.get("method", [])]
    lines += ["", "```json", json.dumps(meta.get("versions", {}), indent=2), "```", "",
              "## Threats to validity", ""] + ["- %s" % item for item in meta.get("threats", [])]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":   # Finished runs' totals moved to bench_mcp_gate; this spelling keeps working.
    import sys
    sys.exit(gate.main())
