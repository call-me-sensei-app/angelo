# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The prompt audit's output: the failing lines, the Markdown report, and the JSON files (``prompt_audit.py``).

Personal values (email addresses, paths, user and session ids, dates) are masked in every foreign text written down.
"""

import json
import sys
from collections import Counter
from pathlib import Path

import prompt_audit as audit
import prompt_recordings as rec


def _cut(text, size=160):
    text = " ".join(text.split())
    return text if len(text) <= size else text[:size - 1] + "…"


def _rate(hit, total):
    return "%d/%d" % (hit, total) if total else "-"


def gap_lines(rows, key, registry):
    table, with_fact = audit.gaps(rows, key)
    has_stale = {fact["id"] for fact in registry["facts"] if fact.get("stale")}
    lines = ["| Route | Build | Fact | Models | Recordings (trials) | Scripted | Used a stale name | Names | "
             "Same model with the fact: stale/scripted |", "|---|---|---|---|---|---|---|---|---|"]
    for (route, build, fact), entry in sorted(table.items()):
        model = entry["models"].most_common(1)[0][0]
        control = with_fact.get((None if model == "?" else model, fact), [0, 0])
        names = ", ".join("%s ×%d" % item for item in entry["names"].most_common(3))
        lines.append("| %s | %s | %s | %s | %d (%d) | %d | %s | %s | %s |" % (
            route, build, fact, ", ".join("%s %d" % m for m in entry["models"].most_common()), entry["recordings"],
            len(entry["trials"]), entry["scripted"], _rate(entry["stale"], entry["scripted"]), names,
            _rate(control[1], control[0]) if fact in has_stale else "n/a"))
    return lines, table


def foreign_lines(rows, full_out=None):
    versions = audit.foreign_inventory(rows)
    lines, previous = [], {}
    for key in sorted(versions, key=lambda k: (k[0], audit.version_key(k))):
        entry = versions[key]
        runtime, version = key
        size = entry["bytes"].most_common(1)[0][0] if entry["bytes"] else 0
        lines += ["", "### %s %s: %d recordings (%s)" % (runtime, version, entry["recordings"],
                                                         ", ".join("%s %d" % r for r in entry["routes"].most_common())),
                  "", "System text Angelo did not write: %d B (most common size), %d paragraphs. Tools: %s." % (
                      size, len(entry["paragraphs"]), "; ".join(entry["tools"]) or "none"),
                  "Injected into the first message: %s." % (
                      "; ".join("%s (×%d)" % (_cut(text, 120), count) for text, count in entry["injected"].most_common(6))
                      or "nothing")]
        before = previous.get(runtime)
        if before is not None:
            added = [p for p in entry["paragraphs"] if p not in before["paragraphs"]]
            removed = [p for p in before["paragraphs"] if p not in entry["paragraphs"]]
            tools = sorted(set(entry["tools"]) ^ set(before["tools"]))
            lines.append("Changed from the previous version: %d paragraphs added, %d removed%s." % (
                len(added), len(removed), (", tools " + "; ".join(tools)) if tools else ""))
            lines += ["- added: " + _cut(p) for p in added[:12]] + ["- removed: " + _cut(p) for p in removed[:12]]
        previous[runtime] = entry
    if full_out:
        with open(full_out, "w") as handle:
            for runtime, entry in sorted(previous.items()):   # the latest version of each runtime
                version = next(k[1] for k, v in versions.items() if v is entry)
                handle.write("===== %s %s: system text Angelo did not write (masked) =====\n\n" % (runtime, version))
                handle.write("\n\n".join(p for p, _ in entry["paragraphs"].most_common()) + "\n\n")
                handle.write("===== tools =====\n" + "\n".join(entry["tools"]) + "\n\n")
                handle.write("===== injected =====\n" + "\n\n".join(entry["injected"]) + "\n\n")
    return lines


def digest_lines(table):
    lines = ["| Route | Build | Recordings | Angelo text (B) | Angelo text digests | Tools digests | Foreign digests | Runtime |",
             "|---|---|---|---|---|---|---|---|"]
    for route, builds in sorted(table.items()):
        for build, entry in sorted(builds.items(), key=lambda item: item[1]["first"] or ""):
            lines.append("| %s | %s | %d | %s | %s | %s | %s | %s |" % (
                route, build, entry["recordings"], ", ".join(str(b) for b in sorted(map(int, entry["bytes"]))),
                ", ".join(entry["angelo_text"]), len(entry["angelo_tools"]), len(entry["foreign"]),
                ", ".join(sorted(entry["runtime"]))))
    return lines


def write(rows, registry, arguments):
    failing = [row for row in rows if row["missing"] or row["leaked"] or row["foreign"]]
    lines, table = gap_lines(rows, "missing", registry)
    for (route, build, fact), entry in sorted(table.items()):
        print("prompt audit: %s @ %s lacks %s in %d recordings (%d trials), e.g. %s" % (
            route, build, fact, entry["recordings"], len(entry["trials"]), entry["examples"][0]), file=sys.stderr)
    leaks, leaked = gap_lines(rows, "leaked", registry)
    for (route, build, fact), entry in sorted(leaked.items()):
        print("prompt audit: %s @ %s reads only its own text but received %s in %d recordings (%d trials), e.g. %s" % (
            route, build, fact, entry["recordings"], len(entry["trials"]), entry["examples"][0]), file=sys.stderr)
    foreign, off = gap_lines(rows, "foreign", registry)
    for (route, build, entry_id), entry in sorted(off.items()):
        print("prompt audit: %s @ %s received %s, which Angelo turns off, in %d recordings (%d trials), e.g. %s" % (
            route, build, entry_id, entry["recordings"], len(entry["trials"]), entry["examples"][0]), file=sys.stderr)
    uncovered = audit.coverage(rows, registry, arguments.build) if arguments.require_routes else []
    for route, build in uncovered:
        print("prompt audit: no recording of %s%s: record it (gate or prompt_route_probe.py) before release" % (
            route, " @ " + build if build else ""), file=sys.stderr)
    routes = Counter(row["route"] for row in rows)
    print("prompt audit: %d recordings (%s); %d lack a fact their route must receive; %d received another route's fact" % (
        len(rows), ", ".join("%s %d" % r for r in sorted(routes.items())), sum(bool(row["missing"]) for row in rows),
        sum(bool(row["leaked"]) for row in rows)))
    table_digests = audit.digests(rows)
    if arguments.digests:
        Path(arguments.digests).write_text(json.dumps(table_digests, indent=1, sort_keys=True))
    drift = []
    if arguments.baseline:
        drift = audit.drift(table_digests, json.loads(Path(arguments.baseline).read_text()))
        for line in drift:
            print("prompt audit: " + line)
    if arguments.json:
        Path(arguments.json).write_text(json.dumps([dict(
            path=row["recording"]["path"], shape=row["recording"]["shape_index"], route=row["route"],
            build=row["recording"]["build"], trial=row["recording"]["trial"], kind=row["recording"]["kind"],
            model=row["recording"]["model"], runtime=row["recording"]["runtime"],
            runtime_version=row["recording"]["runtime_version"], timestamp=row["recording"]["timestamp"],
            scripts=len(row["recording"]["scripts"]), missing=row["missing"], review=row["review"], stale=row["stale"], leaked=row["leaked"], foreign=row["foreign"],
            angelo_text=rec.digest(row["recording"]["angelo_text"])) for row in rows], indent=0))
    if arguments.report:
        review, _ = gap_lines(rows, "review", registry)
        uncovered = sorted(set(registry["routes"]) - set(routes) - {"other"})
        out = ["# Prompt audit", "",
               "What each model RECEIVED, read from the recorded requests (Claude `api-request-shape`, Codex "
               "`session_meta` and first-turn developer messages), never from the source. Registry: %d facts. "
               "Recordings: %d, by route: %s. Routes with no recording: %s." % (
                   len(registry["facts"]), len(rows), ", ".join("%s %d" % r for r in sorted(routes.items())),
                   ", ".join(uncovered) or "none"),
               "", "## Gaps: a fact the route must receive is missing", "",
               "Scripted = recordings whose model ran at least one script. Used a stale name = of those, scripts that "
               "wrote the name the fact replaces. The last column is the control: the same count over the most common "
               "model's recordings, on any route or build, that DID receive the fact (n/a: the fact names no stale "
               "name).", ""] + lines
        out += ["", "## Leaks: a route that reads only its own text received another route's fact", ""] + leaks
        out += ["", "## Runtime text Angelo turns off, still received", ""] + foreign
        out += ["", "## Review: missing, need not decided (not a failure)", ""] + review
        out += ["", "## Foreign text: what the runtime adds around Angelo's text, per runtime version"]
        out += foreign_lines(rows, arguments.foreign)
        out += ["", "## Received digests per route and build", ""] + digest_lines(table_digests)
        if drift:
            out += ["", "## Drift against the baseline", ""] + ["- " + line for line in drift]
        out += ["", "## Unregistered differences (latest recording per route; candidates for the registry)", ""]
        for pair, tokens in audit.unregistered(rows, registry).items():
            out.append("- %s: %s" % (pair, ", ".join(tokens[:40]) + (" …(+%d)" % (len(tokens) - 40) if len(tokens) > 40 else "")))
        Path(arguments.report).write_text("\n".join(out) + "\n")
    return 1 if failing or uncovered else 0
