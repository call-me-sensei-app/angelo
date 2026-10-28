#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Audit what each model actually received, from the recorded requests (doc/angelo/prompt_audit.rst).

The per-model texts are pinned to their bytes (test_native_per_model_text), which says whether a text changed but
not whether each route got each fact. A per-model split once left the Blender 5.2 facts in the Codex text only, and
Opus wrote NISHITA in 446 of 975 recorded trials. This reads the recordings, never the
source, so it also sees what the runtime adds around Angelo's text. Three parts:

* **Registry** (``prompt_registry.json``): every fact once, with the routes meant to get it. Every recorded request
  of such a route must contain it, or the audit fails naming the route, the fact and the recording. A route in
  ``own_text_only`` (Studio's prompt editor) must also receive no fact meant only for other routes: one found in its
  recording is a leak, and fails the same way.
* **Foreign text**: everything in a recorded request that Angelo did not write (the runtime's own system prompt and
  tools, the context it injects into the first message), diffed per runtime version.
* **Digests**: a digest of each route's recorded Angelo text, tools and foreign text per build, so drift shows as a
  diff against a saved baseline (``--baseline``).

    python3 scripts/angelo/prompt_audit.py check <recordings folder>... [--build COMMIT] [--report out.md]
        [--json out.json] [--digests out.json] [--baseline digests.json] [--foreign out.txt]

exits 1 when a recording lacks a fact its route must receive, or an own-text-only route received another route's fact. Standard library only; reads files, runs nothing.
"""

import argparse
import fnmatch
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # prompt_recordings beside this file, also under python -I
import prompt_recordings as rec  # noqa: E402

REGISTRY = Path(__file__).resolve().with_name("prompt_registry.json")
# Identifier-like tokens (dotted or snake_case names, ALL_CAPS enum values) for the unregistered-difference scan.
TOKEN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:[._][A-Za-z0-9_]+)+\b|\b[A-Z][A-Z0-9_]{3,}\b")


def load_registry(path=REGISTRY):
    registry = json.loads(Path(path).read_text())
    problems = registry_problems(registry)
    if problems:
        raise ValueError("prompt registry: " + "; ".join(problems))
    return registry


def registry_problems(registry):
    """A malformed registry fails loudly rather than auditing nothing."""
    problems, ids = [], Counter(fact["id"] for fact in registry["facts"])
    problems += ["fact %s is listed %d times" % (fact, count) for fact, count in ids.items() if count > 1]
    known = set(registry["routes"])
    for fact in registry["facts"]:
        if not fact.get("anchors") or any(not anchor for anchor in fact["anchors"]):
            problems.append("fact %s has an empty anchor" % fact["id"])
        for pattern in fact.get("routes", []) + fact.get("review", []):
            if not expand(registry, [pattern]) and pattern not in registry["aliases"]:
                problems.append("fact %s names unknown route %s" % (fact["id"], pattern))
        overlap = expand(registry, fact.get("routes", [])) & expand(registry, fact.get("review", []))
        if overlap:
            problems.append("fact %s both requires and reviews %s" % (fact["id"], ", ".join(sorted(overlap))))
        for pattern in fact.get("stale", []):
            try:
                re.compile(pattern)
            except re.error as error:
                problems.append("fact %s stale pattern %r: %s" % (fact["id"], pattern, error))
    for alias, patterns in registry["aliases"].items():
        if not expand(registry, patterns):
            problems.append("alias %s names no route" % alias)
    for entry in registry.get("reported_routes", []):
        if not expand(registry, [entry.get("route", "")]) or not entry.get("reason") or not entry.get("owner"):
            problems.append("reported_routes entry %r needs a known route, a reason and an owner" % entry)
    for pattern in registry.get("own_text_only", []):
        if not expand(registry, [pattern]):
            problems.append("own_text_only names unknown route %s" % pattern)
    return problems + ["the markers lack %s" % name for name in
                       ("studio", "mcp", "parts_check", "collaborator", "program_reference", "short_identifiers",
                        "codex_plan", "codex_deferred")
                       if name not in registry.get("markers", {})] + ([] if "other" in known else ["no route other"])


def expand(registry, patterns):
    """Route ids for fnmatch patterns and aliases."""
    routes, out = list(registry["routes"]), set()
    for pattern in patterns:
        for item in registry["aliases"].get(pattern, [pattern]):
            out |= {route for route in routes if fnmatch.fnmatchcase(route, item)}
    return out - {"other"}


def _has(text, marker):
    return any(rec.normalize(m) in text for m in ([marker] if isinstance(marker, str) else marker))


def classify(recording, registry):
    """The route a recording belongs to, from what it holds (never from the harness's flags).

    A probe's recording names the route it exercised; that name counts only when the recording holds nothing of
    Angelo's (an MCP client whose opening request carries no Angelo text), so the gap is reported, not skipped.
    """
    route = _classify(recording, registry)
    declared = recording.get("declared_route")
    return declared if route == "other" and declared in registry["routes"] else route


def _classify(recording, registry):
    markers, text = registry["markers"], rec.normalize(recording["angelo_text"])
    opening = recording["opening"]
    tools = [tool.get("name") or "" for tool in recording["angelo_tools"] + recording["foreign_tools"]]
    if opening == markers["studio"]:
        return "studio-" + recording["runtime"]
    if opening == markers["parts_check"]:
        return "parts-check-" + recording["runtime"]
    if opening and _has(text, markers["collaborator"]):   # Angelo's text plus the delegation's own
        return "collaborator-" + recording["runtime"]
    if opening == markers["mcp"]:
        return "mcp-claude-code" if recording["runtime"] == "claude" else "mcp-codex"
    if recording["runtime"] == "claude":
        if opening:
            if "StructuredOutput" in tools:
                return "claude-plan"
            return "claude-direct" if any(name.startswith("mcp__angelo__") for name in tools) else "claude-other"
        return "mcp-claude-code" if any(name.startswith("mcp__angelo__") for name in tools) else "other"
    if not opening:
        return "mcp-codex" if recording.get("mcp_client") else "other"
    if recording.get("extra_text") == "voice":
        return "voice"
    if not _has(text, markers["program_reference"]):
        return "codex-legacy"
    return "codex-%s-%s%s" % ("deferred" if _has(text, markers["codex_deferred"]) else "full",
                              "plan" if _has(text, markers["codex_plan"]) else "chat",
                              "" if _has(text, markers["short_identifiers"]) else "-long")


def received(recording):
    """Everything Angelo gave the model in this request: its text and its tools (names, descriptions, schemas)."""
    tools = json.dumps(recording["angelo_tools"], ensure_ascii=False)
    # Schema text arrives JSON-escaped; unescape the common sequences so a sentence in a description still matches.
    tools = tools.replace("\\n", " ").replace('\\"', '"')
    return rec.normalize(recording["angelo_text"] + "\n" + tools)


def fact_present(fact, text):
    for anchor in fact["anchors"]:
        options = anchor if isinstance(anchor, list) else [anchor]
        if not any(rec.normalize(option) in text for option in options):
            return False
    return True


def stale_uses(fact, recording):
    scripts = "\n".join(recording["scripts"])
    return sorted({m.group(0) for p in fact.get("stale", []) for m in re.finditer(p, scripts)})


def check_text(route, text, registry):
    """``{missing, review, present, leaked}`` fact ids for what ``route`` received (``received``'s form).

    The one per-route rule, for a recording and for text assembled from source alike: a fact the route must
    receive and lacks is missing; one it reads without being meant to, on an own-text-only route, is a leak.
    """
    out = dict(missing=[], review=[], present=[], leaked=[])
    own = route in expand(registry, registry.get("own_text_only", []))
    for fact in registry["facts"]:
        required = route in expand(registry, fact.get("routes", []))
        reviewed = route in expand(registry, fact.get("review", []))
        present = fact_present(fact, text)
        if required or reviewed:
            out["present" if present else "missing" if required else "review"].append(fact["id"])
        elif own and present:
            out["leaked"].append(fact["id"])
    return out


def foreign_present(registry, route, recording):
    """Ids of ``foreign_absent`` entries this recording carries: runtime text or tools Angelo turned off."""
    found = []
    text = rec.normalize("\n".join(recording["foreign_blocks"] + recording["injected"]))
    tools = [tool.get("name") or tool.get("type") or "" for tool in recording["foreign_tools"]]
    for entry in registry.get("foreign_absent", []):
        if recording["runtime"] != entry["runtime"] or route not in expand(registry, entry["routes"]):
            continue
        if any(rec.normalize(anchor) in text for anchor in entry.get("anchors", [])) or any(
                fnmatch.fnmatchcase(name, pattern) for pattern in entry.get("tools", []) for name in tools):
            found.append(entry["id"])
    return found


def turn_missing(registry, route, recording):
    """Ids of ``turn_facts`` a first message lacks when its condition holds (an attached image)."""
    text = rec.normalize("\n".join(recording.get("turn_texts", [])))
    return [fact["id"] for fact in registry.get("turn_facts", [])
            if route in expand(registry, fact["routes"]) and (fact["when"] != "image" or recording.get("turn_image"))
            and not fact_present(fact, text)]


def audit(recordings, registry):
    """Per-recording results: route, missing required facts (opening and turn), missing review facts, stale names
    used, leaks (another route's facts on a route that reads only its own text) and runtime text Angelo turned off."""
    rows = []
    for recording in recordings:
        route = classify(recording, registry)
        if route == "other":
            continue
        row = dict(recording=recording, route=route, stale={}, **check_text(route, received(recording), registry))
        if recording.get("retrieved") and not recording.get("listing_complete"):
            # Judged on part of a listing, a fact would read as missing: the run is reported as cut instead.
            row.update(missing=["probe.listing_truncated"], review=[], present=[])
        row["missing"] += turn_missing(registry, route, recording)
        row["foreign"] = foreign_present(registry, route, recording)
        for fact in registry["facts"]:
            used = stale_uses(fact, recording) if fact["id"] in row["missing"] + row["present"] + row["review"] else []
            if used:
                row["stale"][fact["id"]] = used
        rows.append(row)
    return rows


def data_parity(tree=None):
    """Route parity for data (``route_parity.py``, the tree's own, so it imports that tree's add-on): the number of
    problems, after printing them. A tree without it has nothing to check."""
    import subprocess
    script = Path(tree or Path(__file__).resolve().parents[2]) / "scripts/angelo/route_parity.py"
    if not script.is_file():
        return 0
    completed = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    problems = [line for line in completed.stdout.splitlines() if line.startswith("PROBLEM")]
    for line in problems:
        print("prompt audit (data): %s" % line[len("PROBLEM "):], file=sys.stderr)
    print("prompt audit (data): %d problems" % len(problems))
    return len(problems) or completed.returncode


def audit_sources(registry, tree=None):
    """``[(route, variant, kind, fact)]`` for every route assembled from source: a missing fact or a leak."""
    import prompt_sources
    problems = []
    for route, variant, text, tools in prompt_sources.assemble(*([tree] if tree else [])):
        result = check_text(route, received(prompt_sources.recording(text, tools)), registry)
        problems += [(route, variant, "lacks", fact) for fact in result["missing"]]
        problems += [(route, variant, "leaks", fact) for fact in result["leaked"]]
    return problems


def coverage(rows, registry, builds):
    """Release routes with no recording among ``rows`` (for each build asked for, or overall)."""
    seen = {(row["route"], row["recording"]["build"] or "") for row in rows}
    out = []
    for route in sorted(expand(registry, registry.get("release_routes", []))):
        for build in builds or [None]:
            if not any(r == route and (build is None or b.startswith(build)) for r, b in seen):
                out.append((route, build))
    return out


def label(row):
    recording = row["recording"]
    return "%s#%d" % (recording["path"], recording["shape_index"])


def gaps(rows, key):
    """(route, build, fact) -> recordings missing it, trials, stale-name uses with and without the fact."""
    table = defaultdict(lambda: dict(recordings=0, trials=set(), scripted=0, stale=0, examples=[], names=Counter(),
                                     models=Counter()))
    # The control: (model, fact) -> [scripted recordings that received the fact, of them stale], any route or build.
    with_fact = defaultdict(lambda: [0, 0])
    for row in rows:
        recording = row["recording"]
        for fact in row["present"]:
            if recording["scripts"]:
                with_fact[(recording["model"], fact)][0] += 1
                with_fact[(recording["model"], fact)][1] += bool(row["stale"].get(fact))
        for fact in row[key]:
            entry = table[(row["route"], recording["build"] or "unknown", fact)]
            entry["recordings"] += 1
            entry["trials"].add(recording["trial"])
            entry["scripted"] += bool(recording["scripts"])
            entry["stale"] += bool(row["stale"].get(fact))
            entry["names"].update(row["stale"].get(fact, []))
            entry["models"][recording["model"] or "?"] += 1
            if len(entry["examples"]) < 3:
                entry["examples"].append(label(row))
    return table, with_fact


def foreign_inventory(rows):
    """Per (runtime, version): masked foreign paragraphs, foreign tools and injected context, with trial counts."""
    versions = defaultdict(lambda: dict(paragraphs=Counter(), tools=Counter(), injected=Counter(), recordings=0,
                                        routes=Counter(), bytes=Counter()))
    for row in rows:
        recording = row["recording"]
        entry = versions[(recording["runtime"], recording["runtime_version"] or "?")]
        entry["recordings"] += 1
        entry["routes"][row["route"]] += 1
        system = "\n\n".join(recording["foreign_blocks"])
        entry["bytes"][len(system.encode())] += 1
        for paragraph in re.split(r"\n\s*\n", rec.mask(system)):
            if paragraph.strip():
                entry["paragraphs"][paragraph.strip()] += 1
        for tool in recording["foreign_tools"]:
            entry["tools"]["%s (%s, %d B)" % (tool.get("name") or tool.get("type"), rec.digest(tool),
                                              len(json.dumps(tool)))] += 1
        for text in recording["injected"]:
            entry["injected"][rec.mask(text.strip())] += 1
    return versions


def version_key(version):
    return tuple(int(part) if part.isdigit() else 0 for part in re.split(r"[.\-]", version[1]))


def digests(rows):
    """route -> build -> what was received, by digest, with recording counts and runtime versions."""
    out = defaultdict(lambda: defaultdict(lambda: dict(angelo_text=Counter(), angelo_tools=Counter(),
                                                       foreign=Counter(), runtime=Counter(), recordings=0,
                                                       first=None, last=None, bytes=Counter())))
    for row in rows:
        recording = row["recording"]
        entry = out[row["route"]][recording["build"] or "unknown"]
        entry["recordings"] += 1
        entry["angelo_text"][rec.digest(recording["angelo_text"])] += 1
        entry["bytes"][len(recording["angelo_text"].encode())] += 1
        entry["angelo_tools"][rec.digest(recording["angelo_tools"])] += 1
        entry["foreign"][rec.digest([rec.mask(b) for b in recording["foreign_blocks"]] + recording["foreign_tools"])] += 1
        entry["runtime"]["%s %s" % (recording["runtime"], recording["runtime_version"])] += 1
        stamp = recording["timestamp"] or ""
        entry["first"] = min(filter(None, [entry["first"], stamp])) if stamp else entry["first"]
        entry["last"] = max(filter(None, [entry["last"], stamp])) if stamp else entry["last"]
    return json.loads(json.dumps(out))


def drift(current, baseline):
    """Lines naming every route/build whose received digests differ from the baseline's."""
    lines = []
    for route, builds in sorted(current.items()):
        for build, entry in sorted(builds.items()):
            before = baseline.get(route, {}).get(build)
            if before is None:
                lines.append("new: %s @ %s" % (route, build))
                continue
            for part in ("angelo_text", "angelo_tools", "foreign"):
                if set(entry[part]) != set(before[part]):
                    lines.append("drift: %s @ %s %s %s -> %s" % (route, build, part, sorted(before[part]),
                                                                  sorted(entry[part])))
    return lines


def unregistered(rows, registry):
    """Identifier tokens one route's latest recording states that another route's lacks, in no registry fact.

    A candidate for the registry, never a failure: a fact a route does not need is a decision, not a gap.
    """
    anchors = rec.normalize(json.dumps([fact["anchors"] for fact in registry["facts"]]))
    latest = {}
    for row in rows:
        if row["route"] in expand(registry, ["agent"]) and row["route"] != "codex-legacy":
            if row["route"] not in latest or (row["recording"]["timestamp"] or "") > (latest[row["route"]]["recording"]["timestamp"] or ""):
                latest[row["route"]] = row
    out = {}
    for source, row in sorted(latest.items()):
        tokens = set(TOKEN.findall(row["recording"]["angelo_text"]))
        for target, other in sorted(latest.items()):
            if target != source:
                text = received(other["recording"])
                missing = sorted(t for t in tokens if t not in text and t not in anchors)
                if missing:
                    out["%s -> %s" % (source, target)] = missing
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit what each model received, from recorded requests.")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="check recordings against the fact registry")
    check.add_argument("roots", nargs="+", help="folders (or files) holding Claude transcripts and Codex rollouts")
    check.add_argument("--registry", default=str(REGISTRY))
    check.add_argument("--build", action="append", default=[], help="only recordings of this add-on commit (prefix)")
    check.add_argument("--report", help="write the Markdown report here")
    check.add_argument("--json", help="write per-recording results here")
    check.add_argument("--digests", help="write per-route digests per build here")
    check.add_argument("--baseline", help="a digests file from an earlier run: report drift against it")
    check.add_argument("--foreign", help="write the latest runtime versions' full foreign text here (masked)")
    check.add_argument("--require-routes", action="store_true",
                       help="fail when a release route has no recording (of each --build)")
    sources = commands.add_parser("sources", help="check every route's text assembled from a source tree")
    sources.add_argument("--registry", default=str(REGISTRY))
    sources.add_argument("--tree", default=None, help="the source tree (default: this checkout)")
    arguments = parser.parse_args(argv)
    registry = load_registry(arguments.registry)
    if arguments.command == "sources":
        problems = audit_sources(registry, arguments.tree)
        for route, variant, kind, fact in problems:
            print("prompt audit (source): %s (%s) %s %s" % (route, variant, kind, fact), file=sys.stderr)
        print("prompt audit (source): %d problems" % len(problems))
        return 1 if problems or data_parity(arguments.tree) else 0
    found = rec.recordings(arguments.roots, registry["openings"])
    if arguments.build:
        found = (r for r in found if r["build"] and any(r["build"].startswith(b) for b in arguments.build))
    rows = audit(found, registry)
    import prompt_audit_report as report  # noqa: E402  (beside this file)
    return report.write(rows, registry, arguments)


if __name__ == "__main__":
    sys.exit(main())
