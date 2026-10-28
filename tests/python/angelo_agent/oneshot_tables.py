# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The tables of a one-shot report: the sides of one test next to each other, and several trials at a glance.

* ``table``: one column per side and trial, one row per path metric
  (``oneshot_metrics``), with the verdict and check counts on top.
* ``intended_path``: Angelo's one line: one message, no error-ended turn,
  the typed share of mutations, checks passed.
* Three verdicts, everywhere: a trial is ``passed``, ``failed`` or
  ``inconclusive`` (its only unmet checks are ones the grader could not
  decide; never a pass), and a check count is passed / failed / inconclusive
  of all (``checks_text``).
* ``trials`` and ``trials_table``: one trial is a sample of one (the same
  test took 144 s, then 229 s by another route), so when a side ran a test
  several times the report gives the passed, failed and inconclusive counts
  and, over the trials, the
  median and the range of the seconds, the output tokens, the typed share of
  mutations and the lookups before the first change, with how the turns
  ended. A number a trial did not record is left out of its row's median and
  counted as unknown, never as zero.

Standard library only.
"""

import re
import statistics

import bench_totals
import oneshot_metrics as metrics


def checks_text(checks):
    """``12 / 1 / 1 of 14``: a trial's closed checks passed, failed and inconclusive, of all of them.

    ``checks`` is a scorecard's ``{"passed", "total", "failed": [unmet entries]}``; an unmet entry marked
    ``inconclusive`` is one the grader could not decide.
    """
    checks = checks or {}
    unmet = checks.get("failed") if isinstance(checks.get("failed"), list) else []
    undecided = sum(bool(entry.get("inconclusive")) for entry in unmet) or checks.get("inconclusive") or 0
    # Every check not passed and not undecided failed; a grading error is a failed entry with no total behind it.
    failed = max(checks.get("total", 0) - checks.get("passed", 0) - undecided, len(unmet) - undecided)
    return "%d / %d / %d of %d" % (checks.get("passed", 0), failed, undecided, checks.get("total", 0))


def verdicts_line(sides):
    """``Angelo passed 1, failed 0, inconclusive 1 of 2 trials; ...``: every side's verdicts over its trials."""
    grouped = {}
    for side in sides:
        grouped.setdefault(side_name(side), []).append(side["verdict"])
    return "; ".join("%s %s" % (name, _verdict_text(verdicts, "trials")) for name, verdicts in grouped.items())


def _verdict_text(verdicts, unit):
    passed, undecided = verdicts.count("passed"), verdicts.count("inconclusive")
    return "passed %d, failed %d, inconclusive %d of %d %s" % (
        passed, len(verdicts) - passed - undecided, undecided, len(verdicts), unit)


def _named(counts):
    return ", ".join("%s %s" % (name, value) for name, value in (counts or {}).items()) or "none"


def _yes(value):
    return {True: "yes", False: "no"}.get(value, "unknown")


def _delivered(looked):
    """How many of the agent's own pictures reached the model: confirmed, and those sent unconfirmed apart."""
    confirmed, unconfirmed = looked["delivered_to_model"], looked.get("delivered_unconfirmed")
    if looked.get("delivery") == "unknown" and not confirmed:
        return "unknown (%s sent, delivery not confirmed)" % (unconfirmed or "some")
    if unconfirmed:
        return "%s (+%d sent, delivery not confirmed)" % (confirmed, unconfirmed)
    return metrics.unknown(confirmed)


def _usage_text(usage):
    if not usage:
        return "unknown"
    return "%s (%s) / %s" % (usage.get("input_tokens"), metrics.unknown(usage.get("cached_input_tokens")),
                             usage.get("output_tokens"))


ROWS = (
    ("Messages typed", lambda p: metrics.unknown(p["messages_typed"])),
    ("Stop reason", lambda p: p["stop"]["reason"] + (" (%s)" % p["stop"]["detail"][:120]
                                                     if p["stop"]["detail"] else "")),
    ("Model requests", lambda p: metrics.unknown(p["model_requests"])),
    ("Rounds", lambda p: metrics.unknown(p["rounds"])),
    ("Tool calls, by tool", lambda p: "%d: %s" % (p["tool_calls"]["total"], _named(p["tool_calls"]["by_tool"]))),
    ("Mutations: typed calls / script calls (typed share)", lambda p: "%d / %d (%s)" % (
        p["mutations"]["typed_calls"], p["mutations"]["script_calls"],
        "–" if p["mutations"]["typed_share"] is None else "%.0f %%" % (100 * p["mutations"]["typed_share"]))),
    ("Scripts by category", lambda p: _named(p["mutations"]["script_categories"])),
    ("Why each script (from the request's own steps) / scripts that raised", lambda p: "–"
     if "script_reasons" not in p["mutations"] else "%s / %s" % (
         "unknown" if p["mutations"]["script_reasons"] is None else _named(metrics._count(
             p["mutations"]["script_reasons"])) + (" (%d later steps not recorded)" % p["mutations"]["steps_dropped"]
                                                   if p["mutations"].get("steps_dropped") else ""),
         metrics.unknown(p["mutations"].get("scripts_raised")))),
    ("Each mutation in order", lambda p: "; ".join(p["mutations"]["sequence"]) or "none"),
    ("Lookups before the first change, by tool", lambda p: "%d: %s" % (
        p["lookups"]["calls"], _named(p["lookups"]["by_tool"])) if p["lookups"]["calls"] else "0"),
    ("Capability lookups / lookup rounds / rejected plans", lambda p: "–"
     if p["lookups"]["capability_lookups"] is None else "%s / %s / %s" % (
         p["lookups"]["capability_lookups"], metrics.unknown(p["lookups"]["lookup_rounds"]),
         metrics.unknown(p["lookups"]["plan_rejections"]))),
    ("Approvals asked / approved / declined", lambda p: p["approvals"].get("note") or "%d / %d / %d%s" % (
        p["approvals"]["asked"], p["approvals"]["approved"], p["approvals"]["declined"],
        " (%s)" % ", ".join(p["approvals"]["declined_tools"]) if p["approvals"]["declined_tools"] else "")),
    ("Seconds waited on approvals", lambda p: "–" if p["approvals"].get("note")
     else metrics.unknown(p["approvals"]["wait_s"])),
    ("Declined by the harness's approval policy (not a product failure)", lambda p: "–" if p["approvals"].get("note")
     else _named(p["approvals"]["declined_by_policy"])),
    ("Errors: total / recovered in the turn / ended the turn", lambda p: "%d / %d / %d" % (
        p["errors"]["total"], p["errors"]["recovered_in_turn"], p["errors"]["ended_turn"])),
    ("Looked at an image of its own result", lambda p: "%d image(s)%s; reached the model: %s" % (
        p["looked"]["images"], " (%s)" % _named(p["looked"]["view_modes"]) if p["looked"]["view_modes"] else "",
        _delivered(p["looked"]))),
    ("Looked at its result after its last edit", lambda p: _yes(p["looked"].get("after_last_edit"))),
    ("Rendered in the turn", lambda p: ", ".join(p["looked"]["rendered"]) or "no"),
    ("Saw its final render", lambda p: _yes(p["looked"]["final_render_seen"])),
    ("Time to first visible activity, s", lambda p: metrics.unknown(p["first_activity_s"])),
    ("Live row first named the work, s", lambda p: metrics.unknown(p.get("first_progress_s"))),
    ("Wall time, s", lambda p: metrics.unknown(p["wall_s"])),
    ("Tokens in (of which cached) / out", lambda p: _usage_text(p["usage"])),
    ("Output tokens", lambda p: metrics.unknown((p["usage"] or {}).get("output_tokens"))),
)
BASIS_NAMES = {"reported": "Claude Code's own estimate", "list": "list price"}


def cost_basis(paths):
    """The one cost basis of the sides compared (``bench_totals.cost_basis``): each side's turn is one figure."""
    return bench_totals.cost_basis((path.get("cost_reported_usd") for path in paths if path), "sides' turns")


def cost_of(path, basis):
    """A side's cost on the comparison's basis (``cost_basis``), or None."""
    return path.get(bench_totals.PATH_FIELDS[basis["basis"]]) if path else None


def table(columns, basis=None):
    """Markdown rows: ``columns`` is ``[(title, path, {"verdict", "checks"})]``, the sides next to each other.

    A column's ``produced`` (``produced_text``), when given, adds the row of image files and render jobs. The
    cost row prices every side on one ``basis`` (``cost_basis`` of the columns when not given) and names it.
    """
    lines = ["| Path metric | " + " | ".join(title for title, _, _ in columns) + " |",
             "| --- |" + " --- |" * len(columns),
             "| Verdict (closed checks only) | " + " | ".join(extra["verdict"] for _, _, extra in columns) + " |",
             "| Closed checks passed / failed / inconclusive | " + " | ".join(
                 extra["checks"] for _, _, extra in columns) + " |"]
    if any(extra.get("produced") is not None for _, _, extra in columns):
        lines.append("| Image files written and render jobs started in the run | " + " | ".join(
            str(extra.get("produced") or "unknown").replace("|", "/") for _, _, extra in columns) + " |")
    for label, cell in ROWS:
        lines.append("| %s | %s |" % (label, " | ".join(str(cell(path)).replace("|", "/") for _, path, _ in columns)))
    basis = basis or cost_basis([path for _, path, _ in columns])
    lines.append("| Cost, USD (both sides: %s) | %s |" % (BASIS_NAMES[basis["basis"]], " | ".join(
        str(metrics.unknown(cost_of(path, basis))) for _, path, _ in columns)))
    return lines


def intended_path(path, checks_passed, checks_total, checks_inconclusive=0):
    """Angelo's one line: one message, no error-ended turn, the typed share of mutations, checks passed."""
    mutations = path["mutations"]
    share = mutations["typed_share"]
    # One message is one turn: it ended on an error when it did not finish, or its last error was never recovered.
    ended = int(path["stop"]["reason"] != "finished" or bool(path["errors"]["ended_turn"]))
    typed = "no mutation ran" if share is None else "%.0f %% (%d of %d calls)" % (
        100 * share, mutations["typed_calls"], mutations["typed_calls"] + mutations["script_calls"])
    return ("Intended path: one message: %s (%s typed); error-ended turns: %d (stop reason: %s); mutations by typed "
            "tools: %s; checks passed: %d of %d (%d inconclusive)." % (
                "yes" if path["messages_typed"] == 1 else "NO", metrics.unknown(path["messages_typed"]), ended,
                path["stop"]["reason"], typed, checks_passed, checks_total, checks_inconclusive))


def unknown_in(sides):
    """The entries of ``oneshot_metrics.UNKNOWN`` these sides left unknown, with what records each.

    A run against an add-on that records a fact lists nothing for it; a side that never started says nothing.
    """
    keys = set()
    for side in sides:
        path = side.get("path")
        if not path:
            continue
        looked, mutations = path["looked"], path["mutations"]
        if side.get("side") == "mcp":
            keys |= {"mcp.view_mode"} if looked["images"] else set()
            continue
        keys |= {"angelo.image_delivered_to_model"} if looked["delivered_to_model"] is None else set()
        keys |= {"angelo.image_delivery_unconfirmed"} if looked.get("delivery") == "unknown" else set()
        keys |= {"angelo.final_render_seen"} if looked["final_render_seen"] is None else set()
        keys |= {"angelo.codex_rounds"} if side.get("runtime") == "codex" and path["rounds"] is None else set()
        keys |= {"angelo.fallback_reason"} if mutations.get("script_reasons") is None and mutations[
            "script_calls"] else set()
    return {key: metrics.UNKNOWN[key] for key in sorted(keys)}


# ---------------------------------------------------------------- several trials of one test

def side_name(side):
    """A side without its trial number: ``Angelo``, ``Blender MCP (Codex)``."""
    return side.get("side_label") or re.sub(r" \d+$", "", side["label"])


def _spread(values, digits=1, percent=False, none="unknown"):
    """``median [least-most]`` of the known values, with how many were not (``none`` says what that means)."""
    known = [value for value in values if type(value) in (int, float)]
    if not known:
        return none if values else "–"
    scale = 100.0 if percent else 1.0
    text = lambda value: ("%.0f %%" % (value * scale)) if percent else ("%g" % round(value, digits))
    middle = text(statistics.median(known))
    if len(known) > 1:
        middle += " [%s-%s]" % (text(min(known)), text(max(known)))
    missing = len(values) - len(known)
    return middle + (" (%d %s)" % (missing, none) if missing else "")


def trials(sides):
    """Per side (trial numbers dropped): how many trials passed, failed and were inconclusive, and the spread of
    the path over them."""
    grouped = {}
    for side in sides:
        grouped.setdefault(side_name(side), []).append(side)
    rows = []
    for name, group in grouped.items():
        paths = [side["path"] for side in group if side.get("path")]
        stops = metrics._count(path["stop"]["reason"] for path in paths)
        rows.append({
            "side": name, "trials": len(group), "passed": sum(side["verdict"] == "passed" for side in group),
            "inconclusive": sum(side["verdict"] == "inconclusive" for side in group),
            "failed": sum(side["verdict"] not in ("passed", "inconclusive") for side in group),
            "not_started": len(group) - len(paths),
            "seconds": [path["wall_s"] for path in paths],
            "output_tokens": [(path["usage"] or {}).get("output_tokens") for path in paths],
            "typed_share": [path["mutations"]["typed_share"] for path in paths],
            "lookups": [path["lookups"]["calls"] for path in paths],
            "cost_list_usd": [path["cost_list_usd"] for path in paths],
            "cost_reported_usd": [path.get("cost_reported_usd") for path in paths], "stops": stops})
    return rows


TRIAL_COLUMNS = ("Side", "Passed", "Failed", "Inconclusive", "Seconds", "Output tokens", "Typed share of mutations",
                 "Lookups before the first change", "Cost USD", "How the turns ended")
TRIALS_NOTE = ("Inconclusive: the only unmet checks were ones the grader could not decide; never a pass. "
               "Each cell after the three verdict counts is the median over the side's trials, then the least and "
               "the most in brackets. One trial is a sample of one: read a difference between sides against these "
               "ranges.")


def trials_header(test=False):
    """The two header lines of a trials table (``test``: with a leading Test column, for a group of tests)."""
    columns = (["Test"] if test else []) + list(TRIAL_COLUMNS)
    return ["| " + " | ".join(columns) + " |", "| --- |" + " --- |" * (len(columns) - 1)]


def trials_rows(sides, test=None, basis=None):
    """One Markdown row per side: passed, failed and inconclusive of trials, then ``median [least-most]``.

    Costs are on one ``basis`` for every row (``cost_basis`` of these sides when not given); the report states it.
    """
    lines = []
    basis = basis or cost_basis([side.get("path") for side in sides])
    for row in trials(sides):
        cells = ["%d of %d" % (row[key], row["trials"]) for key in ("passed", "failed", "inconclusive")]
        cells += [_spread(row["seconds"]),
                 _spread(row["output_tokens"], 0), _spread(row["typed_share"], percent=True, none="no mutation"),
                 _spread(row["lookups"], 0), _spread(row[bench_totals.PATH_FIELDS[basis["basis"]]], 4),
                 _named(row["stops"]) + (", not started %d" % row["not_started"] if row["not_started"] else "")]
        lines.append("| " + " | ".join(([test] if test else []) + [row["side"]] + cells) + " |")
    return lines
