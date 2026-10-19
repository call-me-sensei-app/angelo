# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One command for a set of one-shot tests (``bench_mcp_vs_angelo.py --task-set oneshot-minis ...``).

A group (``scenario_specs.ONESHOT_GROUPS``) is not a task set of its own:
every member is still its own one-shot test, with a fresh process pair, the
factory scene and exactly one message per side. ``run`` calls the bench once
per member with the same command line, the member's task set and the
member's own folder under ``--output`` (or ``--summarize-only``), in order,
and then writes one table over all of them: ``ONESHOT-GROUP.md`` and
``oneshot-group.json``.

A billed group stops at the first member that does not end cleanly (a
refusal, a provider limit, a side that could not start), so a broken sign-in
never costs one message per member. The unbilled dry run goes through every
member and reports each.
"""

import json
from pathlib import Path

import oneshot_metrics as metrics
import oneshot_report
import oneshot_tables as tables
import scenario_specs

SUMMARY, RESULTS = "ONESHOT-GROUP.md", "oneshot-group.json"
FOLDER_FLAGS = ("--output", "--summarize-only")
UNBILLED_FLAGS = ("--plan-only", "--dry-run-scenario", "--summarize-only")


def _value(argv, flag):
    """The value of ``--flag value`` or ``--flag=value`` in a command line, or None."""
    for index, item in enumerate(argv):
        if item == flag and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith(flag + "="):
            return item.split("=", 1)[1]
    return None


def group_of(argv):
    """The group named by ``--task-set`` in a command line, or None for a task set or no ``--task-set``."""
    name = _value(argv, "--task-set")
    return name if name in scenario_specs.ONESHOT_GROUPS else None


def member_argv(argv, member):
    """The same command line for one member: its task set, and its own folder under each folder flag."""
    output, index = [], 0
    while index < len(argv):
        item = argv[index]
        flag, joined, value = item.partition("=")
        if flag == "--task-set" or flag in FOLDER_FLAGS:
            if not joined:
                index += 1
                value = argv[index] if index < len(argv) else ""
            output += [flag, member if flag == "--task-set" else str(Path(value) / member)]
        else:
            output.append(item)
        index += 1
    return output


def rows(base, members):
    """One row per member and side from each member's ``oneshot-scorecard.json`` (members not run are left out)."""
    found = []
    for member in members:
        path = Path(base) / member / oneshot_report.SCORECARD
        if not path.is_file():
            continue
        card = json.loads(path.read_text())
        for side in card["sides"]:
            found.append({"test": member, "side": side["label"], "label": side["label"],
                          "side_label": tables.side_name(side), "verdict": side["verdict"],
                          "checks": tables.checks_text(side["checks"]), "path": side["path"],
                          "reasons": (side.get("reasons") or []) + ([side["still_missing"]]
                                                                   if side.get("still_missing") else [])})
    return found


def marks(base, members):
    """What the members' scorecards say the group was: the diagnostic stamp of any member (``eval_diagnostics``), a
    cross-model reuse, and each member's scripts label (``bench_integrity``); they carry into the group's files."""
    import eval_diagnostics
    flags, scripts, cross = {}, {}, None
    for member in members:
        path = Path(base) / member / oneshot_report.SCORECARD
        card = json.loads(path.read_text()) if path.is_file() else {}
        flags.update(eval_diagnostics.only_known(card.get("diagnostics")))
        found = card.get("conditions") or {}
        if found.get("scripts"):
            scripts[member] = found["scripts"]
        cross = cross or found.get("cross_model_reuse")
    diagnostics = dict(flags, claim=eval_diagnostics.NOT_A_CLAIM % eval_diagnostics.describe(flags)) if flags else None
    return {"diagnostics": diagnostics, "scripts": scripts, "cross_model_reuse": cross}


def mark_lines(found):
    lines = ["> **%s**" % found["diagnostics"]["claim"], ""] if found.get("diagnostics") else []
    labels = sorted(set(found["scripts"].values()))
    if labels:
        lines += ["Scripts: %s." % ("; ".join(labels) if len(labels) == 1 else "; ".join(
            "`%s` %s" % item for item in sorted(found["scripts"].items()))), ""]
    if found.get("cross_model_reuse"):
        lines += ["**Cross-model reuse in a member:** a tier measurement, never a gate, timing or benchmark figure.",
                  ""]
    return lines


def markdown(group, members, found, codes, stamped=None):
    basis = tables.cost_basis([row["path"] for row in found])
    lines = ["# One-shot group: %s" % group, "", *mark_lines(stamped or {"scripts": {}}),
             "%d tests, each its own one-message run from the factory scene (%s). A side passes a test only on its "
             "closed checks; the columns after that say how it got there." % (len(members), ", ".join(
                 "`%s`" % member for member in members)), "",
             "Cost basis, the same for every test, side and trial: %s." % basis["label"], "",
             "## Each test and side over its trials", "", tables.TRIALS_NOTE, ""] + tables.trials_header(test=True)
    for member in members:
        lines += tables.trials_rows([row for row in found if row["test"] == member], test="`%s`" % member,
                                    basis=basis)
    lines += ["", "Verdicts over all tests and trials: %s." % (tables.verdicts_line(found) or "nothing ran"),
              "", "## Every trial", "",
             "| Test | Side | Verdict | Checks passed / failed / inconclusive | How the turn ended "
             "| Typed / script calls | Each mutation in order | "
             "Lookups before the first change | Output tokens | Seconds | Cost USD |",
             "| --- |" + " --- |" * 10]
    for row in found:
        way = row["path"]
        if not way:
            lines.append("| %s | %s | %s | %s | not started | – | – | – | – | – | – |" % (
                row["test"], row["side"], row["verdict"], row["checks"]))
            continue
        lookups = way["lookups"]
        lines.append("| %s | %s | %s | %s | %s | %d / %d | %s | %s | %s | %s | %s |" % (
            row["test"], row["side"], row["verdict"], row["checks"], way["stop"]["reason"],
            way["mutations"]["typed_calls"], way["mutations"]["script_calls"],
            "; ".join(way["mutations"]["sequence"]).replace("|", "/") or "none",
            "%d (%s)" % (lookups["calls"], tables._named(lookups["by_tool"])) if lookups["calls"] else "0",
            metrics.unknown((way["usage"] or {}).get("output_tokens")), metrics.unknown(way["wall_s"]),
            metrics.unknown(tables.cost_of(way, basis))))
    missing = [member for member in members if member not in {row["test"] for row in found}]
    if missing:
        lines += ["", "Not run: %s." % ", ".join("`%s`" % member for member in missing)]
    declined = sorted({tool for row in found if row["path"]
                       for tool in row["path"]["approvals"].get("declined_tools") or ()})
    if declined:
        lines += ["", "The harness's approval policy declined %s. That is the harness's rule (%s), not a fault of "
                  "the product." % (", ".join(declined), metrics.DECLINE_POLICY)]
    failed = [(row["test"], row["side"], row["verdict"], row["reasons"]) for row in found if row["verdict"] != "passed"]
    if failed:
        lines += ["", "## Why a side did not pass", ""] + [
            "- `%s`, %s (%s): %s" % (test, side, verdict, "; ".join(reasons[:12]))
            for test, side, verdict, reasons in failed]
    lines += ["", "Each test's own report (`%s`, `%s`) is in its folder. Exit codes: %s." % (
        oneshot_report.REPORT, oneshot_report.PAGE, ", ".join("%s %s" % item for item in codes.items()) or "none")]
    return "\n".join(lines) + "\n"


def summarize(base, group, codes):
    """Write the group's table and JSON into ``base``; returns the rows."""
    members = scenario_specs.ONESHOT_GROUPS[group]
    found = rows(base, members)
    base = Path(base)
    over = {member: tables.trials([row for row in found if row["test"] == member]) for member in members}
    stamped = marks(base, members)
    (base / RESULTS).write_text(json.dumps({"group": group, "members": list(members), "exit_codes": codes,
                                            "cost_basis": tables.cost_basis([row["path"] for row in found]),
                                            **{key: value for key, value in stamped.items() if value},
                                            "trials": over, "rows": found}, indent=2, default=str))
    (base / SUMMARY).write_text(markdown(group, members, found, codes, stamped))
    return found


def run(argv, main):
    """Every member of the group named in ``argv`` through ``main`` (the bench's own), then the group's table."""
    group = group_of(argv)
    members = scenario_specs.ONESHOT_GROUPS[group]
    billed = not any(flag == item.split("=", 1)[0] for item in argv for flag in UNBILLED_FLAGS)
    codes = {}
    for member in members:
        codes[member] = main(member_argv(argv, member))
        if codes[member] and billed:
            print("ONESHOT_GROUP_STOPPED", json.dumps({"after": member, "exit_code": codes[member]}), flush=True)
            break
    base = next((value for value in (_value(argv, flag) for flag in FOLDER_FLAGS) if value), None)
    if base and Path(base).is_dir() and "--plan-only" not in argv:
        found = summarize(base, group, codes)
        print("ONESHOT_GROUP", json.dumps({"summary": str(Path(base) / SUMMARY), "exit_codes": codes, "passed": sum(
            row["verdict"] == "passed" for row in found), "inconclusive": sum(
            row["verdict"] == "inconclusive" for row in found), "sides": len(found)}), flush=True)
    return max(codes.values(), default=0) if all(type(code) is int for code in codes.values()) else 1
