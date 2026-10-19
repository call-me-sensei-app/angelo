# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-side metrics and end-of-session scores of a scenario run of ``bench_mcp_vs_angelo.py`` (host side).

``collect(output, task_set)`` reads the raw trial files of a finished run (it
sends nothing), runs ``scenario_job.py --job capture`` in stock Blender on each
side's saved end-of-session scene, scores it against the ground truth
(``scenario_scoring``) and writes ``scenario-scorecard.json``:

* turns: passed (closed checks), failed, ungraded (a turn no check graded is
  never a pass, ``eval_grading``) and not attempted, completed, timed out,
  per-turn wall time and the failed check labels with their measured values;
* cost: wall time, model calls, tool calls, tokens, the runtime's cost
  estimate (Claude Code's ``cost_usd``; Codex reports none) and the list
  price (``list_price_usd``, when the bench's records are given). A total is
  unknown (None) when a turn that ran did not record its value, never a
  partial sum; a skipped turn adds nothing (``bench_totals``). Every side is
  compared on one basis (``cost_basis``, ``cost_compared_usd``): the estimate
  when every side's total has one, else the list price;
* Python: the MCP side's raw-Python executions; Angelo's reviewed-Python
  requests, approvals and declines;
* Undo: the end-of-session Undo probe (``scenario_hygiene.undo_probe``);
* errors: tool errors, API retries, harness and controller errors;
* hygiene (``scenario_hygiene.report``), end-of-session compliance (every
  turn's checks re-run on the saved scene at the scoring frame) and the view
  scores (silhouette IoU, subject IoU, depth agreement and error);
* Angelo's demo recording, when the run had ``--demo-record``: the 1080p
  H.264 video and poster (``scenario_demo.encode``).
"""

import json
import shutil
from pathlib import Path

import bench_totals as totals
import eval_grading
import scenario_references as references
import scenario_scoring as scoring
import scenario_specs as specs

HERE = Path(__file__).resolve().parent
SIDE_LABELS = {"angelo": "Angelo", "mcp": "Blender MCP"}


def _sum(rows, value):
    """``value(row)`` over the turns that ran, rounded; None when one of them did not record it."""
    total = totals.known_sum(rows, value)
    return None if total is None else round(total, 4)


def _wall_s(milliseconds):
    return round(milliseconds / 1000, 2) if totals.is_number(milliseconds) else None


def _failed_checks(host_check):
    """Failed checks with their measured values; a grading error (the checks never ran) is one entry."""
    if (host_check or {}).get("error"):
        return [{"label": "grading error", "details": str(host_check["error"])[-600:]}]
    results = (host_check or {}).get("results") or []
    return [dict({"label": entry.get("label"), "details": entry.get("details")},
                 **({"inconclusive": True} if entry.get("inconclusive") else {}))
            for entry in results if not entry.get("passed")]


def _usage_totals(rows):
    usage = {field: _sum(rows, lambda row, field=field: (row.get("provider_usage") or {}).get(field)) for field in
             ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "cost_usd", "api_ms")}
    return dict(usage, list_price_usd=_sum(rows, lambda row: row.get("list_price_usd")))


def mcp_trial(folder):
    data = json.loads((folder / "trial.json").read_text())
    rows = data.get("tasks") or []
    python = sum(1 for row in rows for name in row.get("tool_names") or [] if name.endswith("execute_blender_code"))
    turns = [{"turn": row["task"], "status": row.get("status"), "passed": row.get("host_check_passed") is True,
              "grade": eval_grading.state(row),
              "wall_s": _wall_s(row.get("wall_ms")), "api_calls": row.get("api_calls"),
              "tool_calls": row.get("tool_call_count"), "python": sum(1 for n in row.get("tool_names") or []
                                                                       if n.endswith("execute_blender_code")),
              "failed_checks": _failed_checks(row.get("host_check")),
              "references": [entry["view"] for entry in row.get("references") or []]} for row in rows]
    return {"side": "mcp", "runtime": data.get("runtime", "claude"), "turns": turns, "rows": rows,
            "metrics": {"api_calls": _sum(rows, lambda r: r.get("api_calls")),
                        "tool_calls": _sum(rows, lambda r: r.get("tool_call_count")),
                        "python_executions": python, "reviewed_python_requests": None,
                        "approvals": {"requested": 0, "approved": 0, "declined": 0,
                                      "note": "the MCP side has no approval gate"},
                        "errors": {"tool_errors": _sum(rows, lambda r: r.get("tool_errors")),
                                   "api_retries": sum(len(r.get("api_retries") or []) for r in rows),
                                   "permission_denials": sum(len((r.get("result") or {}).get("permission_denials") or [])
                                                             for r in rows),
                                   "trial_error": bool(data.get("error")), "aborted": data.get("aborted")}},
            "scenario": data.get("scenario") or {}, "blend": folder / "final.blend"}


def angelo_trial(folder):
    harness = json.loads((folder / "eval-live-agent.json").read_text())
    trial = (harness.get("trials") or [{}])[0]
    rows = trial.get("tasks") or []
    approved = [name for row in rows for name in row.get("approved") or []]
    declined = [name for row in rows for name in row.get("declined") or []]
    turns = [{"turn": row["task"], "status": row.get("status"), "passed": row.get("host_check_passed") is True,
              "grade": eval_grading.state(row),
              "wall_s": _wall_s(row.get("total_ms")), "model_rounds": row.get("model_rounds"),
              "provider_turn_requests": row.get("provider_turn_requests"),
              "python": row.get("python_fallbacks"), "approved": row.get("approved"), "declined": row.get("declined"),
              "failed_checks": _failed_checks(row.get("host_check")),
              "references": [entry["view"] for entry in row.get("references") or []]} for row in rows]
    failures = {}
    for row in rows:
        for key, value in ((row.get("receipt") or {}).get("counts") or {}).items():
            if key.startswith("failure_") and value:
                failures[key] = failures.get(key, 0) + value
    return {"side": "angelo", "turns": turns, "rows": rows,
            "metrics": {"model_rounds": _sum(rows, lambda r: r.get("model_rounds")),
                        "provider_turn_requests": _sum(rows, lambda r: r.get("provider_turn_requests")),
                        "python_executions": approved.count("angelo_execute_python"),
                        "reviewed_python_requests": _sum(rows, lambda r: r.get("python_fallbacks")),
                        "approvals": {"requested": len(approved) + len(declined), "approved": len(approved),
                                      "declined": len(declined), "declined_tools": sorted(set(declined))},
                        "errors": {"receipt_failures": failures,
                                   "error_turns": sum(r.get("status") == "error" for r in rows),
                                   "trial_error": bool(trial.get("error")), "aborted": trial.get("aborted")}},
            "scenario": trial.get("scenario") or {}, "blend": folder / "trial-01" / "qa" / "final.blend",
            "demo_frames": folder / "trial-01" / "qa" / "demo"}


def capture(name, blend, folder, blender=references.STOCK_BLENDER, samples=48):
    """Ray-cast capture, review renders and final checks of a saved scene (reused when already made)."""
    if (folder / "capture.npz").is_file() and (folder / "final-checks.json").is_file():
        return True
    folder.mkdir(parents=True, exist_ok=True)
    code = references.run_job(blender, ["--job", "capture", "--scenario", name, "--out", str(folder),
                                        "--blend", str(blend), "--samples", str(samples)],
                              folder / ".scratch", folder / "capture.log")
    shutil.rmtree(folder / ".scratch", ignore_errors=True)
    return code == 0


def score_side(name, side, records, blender=references.STOCK_BLENDER):
    """Fill one side's totals, Undo/hygiene and end-of-session scores (``blender``: the stock Blender that captures)."""
    rows = side.pop("rows")
    passed = sum(turn["passed"] for turn in side["turns"])
    ungraded = sum(turn["grade"] == "ungraded" for turn in side["turns"])
    undecided = sum(turn["grade"] == "inconclusive" for turn in side["turns"])
    side["metrics"].update(
        turns_total=len(specs.module(name).TURNS), turns_attempted=len(side["turns"]), turns_passed=passed,
        turns_ungraded=ungraded, turns_inconclusive=undecided,
        turns_failed=len(side["turns"]) - passed - ungraded - undecided,
        turns_completed=sum(turn["status"] == "completed" for turn in side["turns"]),
        turns_timed_out=sum(turn["status"] == "timeout" for turn in side["turns"]),
        wall_s=_sum(side["turns"], lambda turn: turn["wall_s"]), **_usage_totals(records or rows))
    scenario = side.pop("scenario")
    side["undo"] = scenario.get("undo")
    side["hygiene"] = scenario.get("hygiene")
    side["end_of_session_error"] = scenario.get("error") or scenario.get("finish_error")
    frames = side.pop("demo_frames", None)
    if frames is not None and (frames / "frames.json").is_file():
        import scenario_demo
        side["demo"] = dict(scenario_demo.encode(frames, frames.parents[2] / "demo", name),
                            recording=scenario.get("demo"))
    blend = side.pop("blend")
    side["blend"] = str(blend) if blend.is_file() else None
    if not blend.is_file():
        side["scores"] = None
        return side
    folder = blend.parent / "scenario-capture"
    if not capture(name, blend, folder, blender):
        side["scores"] = {"error": "capture failed; see %s" % (folder / "capture.log")}
        return side
    truth = scoring.load(specs.references_dir(name) / "capture.npz")
    mine = scoring.load(folder / "capture.npz")
    module = specs.module(name)
    side["scores"] = scoring.score(truth, mine, module.SUBJECT, list(module.CAMERAS))
    for view in module.CAMERAS:
        scoring.silhouette_png(folder / ("silhouette-%s.png" % view), truth["views"][view], mine["views"][view])
    final = json.loads((folder / "final-checks.json").read_text())
    side["end_of_session_checks"] = end_of_session(final)
    side["capture_folder"] = str(folder)
    return side


def end_of_session(final):
    """Every check re-run on the saved scene: passed, failed and inconclusive counts of the total, with the
    labels of the failed (measured) and of the inconclusive (undecided) ones."""
    counts = eval_grading.check_counts(final)
    unmet = [entry for entry in final.get("results") or () if not entry.get("passed")]
    return dict(counts, failed=[entry["label"] for entry in unmet if not entry.get("inconclusive")],
                failed_count=counts["failed"],
                inconclusive_checks=[entry["label"] for entry in unmet if entry.get("inconclusive")])


def collect(output, task_set, results=None, blender=references.STOCK_BLENDER):
    """The scorecard of every trial in a run folder; written to ``scenario-scorecard.json``.

    ``blender``: the run's stock Blender, which captures each saved scene (the default is the macOS one).
    """
    output = Path(output)
    name = specs.scenario_id(task_set)
    records = (results or {}).get("records") or []
    trials = []
    for folder in sorted(output.glob("mcp-trial-*")):
        if folder.is_dir() and (folder / "trial.json").is_file():
            number = int(folder.name.rsplit("-", 1)[1])
            side = score_side(name, mcp_trial(folder),
                              [r for r in records if r["side"] == "mcp" and r["trial"] == number], blender)
            trials.append(dict(side, trial=number, folder=str(folder)))
    for folder in sorted(output.glob("angelo-trial-*")):
        if folder.is_dir() and (folder / "eval-live-agent.json").is_file():
            number = int(folder.name.rsplit("-", 1)[1])
            side = score_side(name, angelo_trial(folder),
                              [r for r in records if r["side"] == "angelo" and r["trial"] == number], blender)
            trials.append(dict(side, trial=number, folder=str(folder)))
    # One cost basis for every side and trial (bench_totals.cost_basis): a side's total is known only when each
    # turn that ran recorded the value, so Claude Code's estimate applies when every side's total has one.
    basis = totals.cost_basis((side["metrics"].get("cost_usd") for side in trials), "sides' sessions")
    for side in trials:
        side["metrics"]["cost_compared_usd"] = side["metrics"].get(
            "cost_usd" if basis["basis"] == "reported" else "list_price_usd")
    card = {"scenario": name, "plan": specs.plan(name), "references": specs.manifest(name), "trials": trials,
            "cost_basis": basis}
    (output / "scenario-scorecard.json").write_text(json.dumps(card, indent=2, default=str))
    return card
