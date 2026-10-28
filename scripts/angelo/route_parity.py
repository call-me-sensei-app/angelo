#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Route parity for data: what a tool result or turn observation carries reaches every route's model
(doc/angelo/prompt_audit.rst, "Route parity for data").

``prompt_audit`` checks the instruction texts each route receives. Facts also arrive as data: fields the host adds to
tool results and to the turn observation. Each route reads a result through its own presentation code: a Codex
thread's tool call (``providers.tool_content``), Claude's direct-tools call and an MCP app's (``DirectRequest._packed``,
which ``ExternalWork`` and the headless host reuse), a Claude plan's verify round (``plan_results`` with the
continuation observation), the next message's handoff of a plan's results (``routing.handoff``: a certified program's
terse receipt, an unchecked receipt, or the result's excerpt; Claude and Codex plan threads alike) and a Codex plan
correction's receipts (``continuation_receipt``). Four fields reached one route and not another before this check:
the 5.2 facts (text: prompt_audit now), the ASSETS description (tool text), a light's ``shines_from`` (0 of 18 Claude
typed plans) and ``quoted_names`` (dropped by the terse receipt's key list).

The check runs every route's real presentation code on recorded results (``tests/python/angelo_agent/
route_parity_corpus``, refreshed from the native suite) and compares, per field:

* **delete-one**: a key at a location (``live_revisions.objects[]``) reaches a route when removing it from every
  dict there changes that route's text. A key that reaches some route but not another is a parity problem unless a
  registered filter for that route, tool, form and location lists the key in ``drops`` (or ``review``) with a reason.
* **probe**: a new key added at each location shows where a route drops fields nobody has written yet. A location
  where the probe reaches some routes but not another needs a registered filter: a key list (an allowlist) is a
  place a future field silently stops (the class that dropped ``quoted_names``).
* **budget**: a route whose text was cut to its size budget (the excerpt keeps 5,000 characters) loses what lies
  past it. That is reported, never failed, except for a registered result fact, which must survive every budget.

A filter entry, a drop or a review that the corpus no longer exercises is stale, and fails, so the registry follows
the code. Pure dictionaries; imports the add-on's pure modules, no ``bpy``; runs nothing.
"""

import copy
import fnmatch
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # Loaded by path too: its views live beside it.
from route_parity_views import (CORPUS, IDENTIFIER, MAX_DEPTH, PROBE, REGISTRY, RESULT_ROUTES, ROOT,  # noqa: F401
                                VIEWS, _addon, handoff_form, truncated, views)


# --- locations --------------------------------------------------------------------------------------------------

def is_map(node):
    """A dict keyed by data (object names, settings paths) rather than by field names."""
    return any(not IDENTIFIER.match(key) for key in node)


def pattern_text(steps):
    return "".join(("[]" if step == "[]" else ("." + step if index else step)) for index, step in enumerate(steps))


def locations(value):
    """``{pattern steps: depth}`` of every field-keyed dict in ``value`` down to ``MAX_DEPTH`` dict levels."""
    found = {}

    def walk(node, steps, depth):
        if isinstance(node, dict):
            if depth > MAX_DEPTH:
                return
            if not is_map(node):
                found.setdefault(steps, depth)
                for key, child in node.items():
                    walk(child, steps + (key,), depth + 1)
            else:
                for child in node.values():
                    walk(child, steps + ("*",), depth + 1)
        elif isinstance(node, list):
            for child in node:
                walk(child, steps + ("[]",), depth)
    walk(value, (), 1)
    return found


def nodes_at(value, steps):
    """Every dict in ``value`` at pattern ``steps``."""
    nodes = [value]
    for step in steps:
        following = []
        for node in nodes:
            if step == "[]" and isinstance(node, list):
                following.extend(node)
            elif step == "*" and isinstance(node, dict):
                following.extend(node.values())
            elif isinstance(node, dict) and step in node:
                following.append(node[step])
        nodes = following
    return [node for node in nodes if isinstance(node, dict) and not is_map(node)]


def _mutated(result, steps, change):
    copied = copy.deepcopy(result)
    for node in nodes_at(copied, steps):
        change(node)
    return copied


def _reach(tool, base, mutated):
    """``({route: reached}, {route: (form, truncated)})``: which routes' texts change from ``base`` to ``mutated``."""
    after = views(tool, mutated)
    # A key whose removal changes which form a route uses (a certified receipt becomes an excerpt) gates that route's
    # presentation rather than being data on it: None, counted neither way.
    reached = {route: None if after.get(route, (None, None))[1] != form else after[route][0] != text
               for route, (text, form) in base.items()}
    return reached, {route: (form, truncated(route, form, text)) for route, (text, form) in base.items()}


def analyse(tool, result):
    """Every location-key of one result with the routes it reaches, and every location's probe.

    Returns rows ``{"kind": "key"|"probe", "at": pattern, "key": key, "reached": {route: bool},
    "forms": {route: (form, truncated)}}``, shallow locations first; a row whose key reached no route is host-only.
    """
    base = views(tool, result)
    rows = []
    for steps, depth in sorted(locations(result).items(), key=lambda item: (item[1], item[0])):
        keys = []
        for node in nodes_at(result, steps):
            keys += [key for key in node if key not in keys]
        for key in keys:
            reached, forms = _reach(tool, base, _mutated(result, steps, lambda node: node.pop(key, None)))
            rows.append({"kind": "key", "at": steps, "key": key, "reached": reached, "forms": forms})
        reached, forms = _reach(tool, base, _mutated(result, steps, lambda node: node.__setitem__(PROBE, "probe")))
        rows.append({"kind": "probe", "at": steps, "key": PROBE, "reached": reached, "forms": forms})
    return rows


# --- registry -------------------------------------------------------------------------------------------------

def load_registry(path=REGISTRY):
    return json.loads(Path(path).read_text())


def reason(entry, path):
    """The reason ``entry`` gives for dropping ``path``: its own, or its longest registered prefix's
    (``delta`` covers ``delta.geometry[].bounds``)."""
    reasons = entry.get("reasons") or {}
    best = None
    for prefix in reasons:
        if path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "["):
            best = prefix if best is None or len(prefix) > len(best) else best
    return reasons.get(best) if best is not None else None


def registry_problems(registry):
    """Malformed entries: every filter states its routes, tools and reason, and every dropped path has a reason."""
    problems = []
    for entry in registry.get("filters", []):
        for field in ("id", "routes", "tools", "says"):
            if not entry.get(field):
                problems.append("filter %s lacks %s" % (entry.get("id"), field))
        if entry.get("drops_every_field") and not entry.get("at"):
            problems.append("filter %s drops every field but names no location" % entry.get("id"))
        for path in entry.get("drops") or ():
            if not reason(entry, path):
                problems.append("filter %s: drop %s has no reason (reasons names no prefix of it)" % (entry["id"], path))
        for path, note in (entry.get("review") or {}).items():
            if not isinstance(note, str) or not note.strip():
                problems.append("filter %s: review %s has no note" % (entry.get("id"), path))
    for entry in registry.get("budgets", []):
        for field in ("id", "routes", "tools", "forms", "says"):
            if not entry.get(field):
                problems.append("budget %s lacks %s" % (entry.get("id"), field))
    return problems


def like(text, pattern):
    """``fnmatch`` where ``[]`` in a pattern is a list step (``checks[].checks[]``), not a character class."""
    return fnmatch.fnmatchcase(text, pattern.replace("[]", "[[][]]"))


def _matches(entry, route, tool, form, at=None):
    """Whether ``entry`` covers this route, tool and form (and, for a probe, its location ``at``)."""
    return (any(fnmatch.fnmatchcase(route, pattern) for pattern in entry["routes"])
            and any(fnmatch.fnmatchcase(tool, pattern) for pattern in entry["tools"])
            and (not entry.get("forms") or form in entry["forms"])
            and (at is None or any(like(pattern_text(at), pattern) for pattern in entry.get("at") or ())))


def _budget(registry, route, tool, form):
    return next((entry for entry in registry.get("budgets", []) if _matches(entry, route, tool, form)), None)


def host_only(tool, result):
    """Root fields that describe the host, not the scene, which a model's copy of this result leaves out on every
    route (``model_results.model_result``'s own rule: timing always; a successful query's cache and budgets; a
    successful script's consent and Undo bookkeeping). A failed result keeps them: what became of it is the model's."""
    _addon()
    from angelo_agent import model_results
    failed = bool(result.get("error")) or result.get("success") is False
    fields = set(model_results.TIMING)
    if tool in model_results.QUERY_TOOLS and not failed:
        fields.update(model_results.QUERY_DIAGNOSTICS)
    if tool == model_results.SCRIPT_TOOL and not failed and result.get("success") is True:
        fields.update(model_results.SCRIPT_BOOKKEEPING)
    return fields


def path_text(at, key):
    return (pattern_text(at) + "." + key) if at else key


def _within(at, containers):
    return any(len(at) > len(steps) and at[:len(steps)] == steps and at[len(steps)] == key for steps, key in containers)


def classify(registry, tool, rows, used=None, result=None):
    """Findings for one analysed result: ``(level, route, at, key, why, form)`` with level ``problem`` (fails), ``review``
    (a registered open gap), ``budget`` (cut at a registered size budget), ``leak`` (host bookkeeping a route shows)
    or ``filtered`` (a registered drop). ``used`` collects what the result exercised, for the stale check."""
    used = set() if used is None else used
    findings, bookkeeping, inside = [], host_only(tool, result or {}), []
    for row in rows:
        at, key = row["at"], row["key"]
        reaching = [route for route, reached in row["reached"].items() if reached]
        if not reaching or _within(at, inside):
            continue  # Every route leaves it out (parity holds), or it lies inside host bookkeeping.
        if key in bookkeeping and not at:
            inside.append((at, key))
            findings += [("leak", route, pattern_text(at), key, "host bookkeeping in a model's copy",
                          row["forms"][route][0]) for route in reaching]
            continue
        path = path_text(at, key)
        for route, reached in row["reached"].items():
            if reached is not False:
                continue
            form, cut = row["forms"][route]
            if row["kind"] == "probe":
                entry = next((entry for entry in registry.get("filters", []) if _matches(entry, route, tool, form, at)),
                             None)
                budget = None if entry or not cut else _budget(registry, route, tool, form)
                if entry:
                    used.add((entry["id"], "at", pattern_text(at)))
                elif budget:
                    used.add((budget["id"], "budget", None))
                level = "filtered" if entry else "budget" if budget else "problem"
                why = entry["says"] if entry else budget["says"] if budget else (
                    "a new field here would reach %s but not this route: register this filter level" % ", ".join(reaching))
                findings.append((level, route, pattern_text(at), key, why, form))
                continue
            level, why = "problem", "reaches %s but not this route" % ", ".join(reaching)
            for entry in registry.get("filters", []):
                if not _matches(entry, route, tool, form):
                    continue
                if path in (entry.get("review") or {}):
                    level, why = "review", entry["review"][path]
                    used.add((entry["id"], "review", path))
                elif path in (entry.get("drops") or ()):
                    level, why = "filtered", reason(entry, path)
                    used.add((entry["id"], "drops", path))
                elif entry.get("drops_every_field") and _matches(entry, route, tool, form, at):
                    level, why = "review", entry["drops_every_field"]
                    used.add((entry["id"], "at", pattern_text(at)))
                if level != "problem":
                    break
            if level == "problem" and cut:
                budget = _budget(registry, route, tool, form)
                if budget:
                    level, why = "budget", budget["says"]
                    used.add((budget["id"], "budget", None))
            findings.append((level, route, pattern_text(at), key, why, form))
    return findings


def stale(registry, used):
    """Registered levels, drops, reviews and budgets the corpus never exercised: the registry no longer describes
    the code (or the corpus lost the result that showed them)."""
    problems = ["budget %s matched nothing" % entry["id"] for entry in registry.get("budgets", [])
                if (entry["id"], "budget", None) not in used]
    for entry in registry.get("filters", []):
        problems += ["filter %s: level %s matched nothing" % (entry["id"], pattern) for pattern in entry.get("at") or ()
                     if not any(item[0] == entry["id"] and item[1] == "at" and like(item[2], pattern)
                                for item in used)]
        problems += ["filter %s: %s %s matched nothing" % (entry["id"], kind, path)
                     for kind in ("drops", "review") for path in entry.get(kind) or ()
                     if (entry["id"], kind, path) not in used]
    return problems


def propose(findings):
    """Each unregistered drop as the registry line that would register it: ``{(route, form, tool): {"at": [...],
    "drops": [...]}}``, for a person to give reasons to (``--propose``)."""
    proposed = {}
    for _name, tool, level, route, at, key, _why, form in findings:
        if level != "problem":
            continue
        entry = proposed.setdefault((route, form, tool), {"at": set(), "drops": set()})
        if key == PROBE:
            entry["at"].add(at)
        else:
            entry["drops"].add((at + "." if at else "") + key)
    return proposed


def parse_pattern(text):
    """``"live_revisions.objects[]"`` as location steps ``("live_revisions", "objects", "[]")``."""
    return tuple(re.findall(r"\[\]|[^.\[\]]+", text or ""))


def result_fact_problems(facts, corpus=None):
    """Each registered result fact (``prompt_registry.json`` ``result_facts``: ``field``, ``tools``, ``at``,
    ``sample``) placed last at its location in every corpus result of its tools, the worst place for a budget: it
    must reach every route in ``data_routes`` (default all), whatever filters and budgets exist. A fact the host
    adds to results is a fact for every model; no registered drop excuses it."""
    corpus = load_corpus() if corpus is None else corpus
    problems = []
    for fact in facts:
        missing = [key for key in ("id", "field", "tools", "at", "sample") if key not in fact]
        if missing:
            problems.append("result fact %s lacks %s" % (fact.get("id"), ", ".join(missing)))
            continue
        steps, placed = parse_pattern(fact["at"]), 0
        for name, tool, result in corpus:
            if not any(fnmatch.fnmatchcase(tool, pattern) for pattern in fact["tools"]) or not nodes_at(result, steps):
                continue
            with_fact = _mutated(result, steps, lambda node: node.__setitem__(fact["field"], copy.deepcopy(fact["sample"])))
            shown, plain = views(tool, with_fact), views(tool, result)
            placed += 1
            # Reached means the fact's value is in the route's text: each of its words the route's text lacked without
            # it (a word the result already states proves nothing), not merely that the text changed. A form that names
            # a fact without its value, or keeps part of it, does not count. A route whose form the fact changes (a
            # receipt becoming an excerpt) is gated by it, not data on it: skipped.
            words = sample_words(fact["sample"])
            for route, (text, form) in shown.items():
                before, before_form = plain.get(route, ("", None))
                if before_form != form or not any(fnmatch.fnmatchcase(route, pattern)
                                                  for pattern in fact.get("data_routes", ["*"])):
                    continue
                telling = [word for word in words if word not in before]
                if not telling:   # Nothing to look for: the sample must state a value no corpus result already does.
                    problems.append("result fact %s: its sample states no word %s lacks on %s" % (fact["id"], name, route))
                elif any(word not in text for word in telling):
                    problems.append("result fact %s does not reach %s (%s) in %s" % (fact["id"], route, form, name))
        if not placed:
            problems.append("result fact %s: no corpus result of %s has the location %r" % (
                fact["id"], ", ".join(fact["tools"]), fact["at"]))
    return problems


HANDLE = re.compile(r"^scn:")   # Routes that shorten identifiers rewrite handles: never a word to look for.


def sample_words(sample):
    """The words a fact's sample states: every string in it of 3 characters or more but a handle, as JSON writes it
    (escaped)."""
    if isinstance(sample, str):
        return [json.dumps(sample, ensure_ascii=False)[1:-1]] if len(sample) >= 3 and not HANDLE.match(sample) else []
    items = sample.values() if isinstance(sample, dict) else sample if isinstance(sample, list) else ()
    return [word for item in items for word in sample_words(item)]


def load_corpus(folder=CORPUS):
    """Every recorded result in ``folder``: ``[(name, tool, result)]``."""
    items = []
    for path in sorted(Path(folder).glob("*.json")):
        for index, item in enumerate(json.loads(path.read_text())):
            items.append(("%s#%d" % (path.stem, index), item["tool"], item["result"]))
    return items


def check(registry=None, corpus=None):
    """``(findings, problems)`` over the corpus: every finding row, and the failures (unregistered drops and
    filters, stale entries, malformed registry)."""
    registry = load_registry() if registry is None else registry
    corpus = load_corpus() if corpus is None else corpus
    problems = list(registry_problems(registry))
    findings, used = [], set()
    for name, tool, result in corpus:
        for finding in classify(registry, tool, analyse(tool, result), used, result):
            findings.append((name, tool) + finding)
    problems += ["%s %s: %s (%s) drops %s at %s (%s)" % (name, tool, route, form, key, at or "the result", why)
                 for name, tool, level, route, at, key, why, form in findings if level == "problem"]
    problems += stale(registry, used)
    return findings, problems


def summary(findings):
    """Counts by level, and one line per (level, route, form, reason) naming its shortest path and how many paths
    and results it covers."""
    counts, groups = {}, {}
    for name, _tool, level, route, at, key, why, form in findings:
        counts[level] = counts.get(level, 0) + 1
        if level == "filtered":
            continue
        path = (at + "." if at else "") + key if key != PROBE else (at or "the result") + " (new fields)"
        group = groups.setdefault((level, route, form, why), {"paths": set(), "results": set()})
        group["paths"].add(path)
        group["results"].add(name)
    lines = ["%-7s %-23s %-25s %s (%d paths, %d results): %s" % (level, route, form, min(group["paths"], key=len),
                                                                 len(group["paths"]), len(group["results"]), why)
             for (level, route, form, why), group in groups.items()]
    return counts, sorted(lines)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--corpus", default=str(CORPUS))
    parser.add_argument("--registry", default=str(REGISTRY))
    parser.add_argument("--propose", action="store_true",
                        help="print each unregistered drop as registry paths, grouped by route, form and tool")
    args = parser.parse_args(argv)
    findings, problems = check(load_registry(args.registry), load_corpus(args.corpus))
    if args.propose:
        for (route, form, tool), entry in sorted(propose(findings).items()):
            print(json.dumps({"route": route, "form": form, "tool": tool, "at": sorted(entry["at"]),
                              "drops": sorted(entry["drops"])}))
        return 0
    counts, lines = summary(findings)
    print("\n".join(lines))
    print("findings:", ", ".join("%s %d" % item for item in sorted(counts.items())))
    for problem in problems:
        print("PROBLEM", problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
