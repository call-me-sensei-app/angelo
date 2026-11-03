# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Early adoption of newer upstream Blender features (AGENTS.md, "Newer Blender first"), used by
``upstream_overlap.py``.

* ``backport COMMIT ...``: how big each upstream commit is and whether it applies to the branch as it stands
  (``git merge-tree``, no working tree touched). A conflicting file is named with why: Angelo changed it, it is
  missing here, or upstream changed it earlier in commits Angelo lacks (the prerequisites to backport first).
* ``early [--target REF]``: every item in ``early_adoption.json`` against what upstream ships at ``REF`` (the newest
  release branch by default, the base Angelo moves to): the backported commit is there or not, what upstream changed
  in the item's files since, and whether the base move recorded its decision (keep ours, take theirs or merge, judged
  on quality, latency and cost).

The registry holds one entry per feature Angelo brings in before its upstream release::

    {"id": "delaunay-node", "title": "Delaunay Triangulation node", "upstream_version": "5.4",
     "upstream_commits": ["<full sha>"], "angelo": "PR #123",
     "paths": ["source/blender/nodes/geometry/nodes/node_geo_delaunay_triangulation.cc"],
     "decisions": {"5.4": {"choice": "take theirs", "why": "identical once 5.4 ships; no latency or cost change"}}}

``upstream_commits`` is empty when Angelo built its own; ``paths`` are the files that hold the feature in Angelo.
"""

import json
import re
from pathlib import Path

REGISTRY = Path(__file__).resolve().with_name("early_adoption.json")
CHOICES = ("keep ours", "take theirs", "merge")
_FIELDS = {"id": str, "title": str, "upstream_version": str, "upstream_commits": list, "angelo": str, "paths": list}


class RegistryError(ValueError):
    pass


def load_registry(path=REGISTRY):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data.get("items")
    if not isinstance(items, list):
        raise RegistryError(f"{path}: 'items' must be a list")
    seen = set()
    for item in items:
        for field, kind in _FIELDS.items():
            if not isinstance(item.get(field), kind):
                raise RegistryError(f"{path}: item {item.get('id', '?')!r} needs {field} ({kind.__name__})")
        if item["id"] in seen:
            raise RegistryError(f"{path}: duplicate id {item['id']!r}")
        seen.add(item["id"])
        if not re.fullmatch(r"\d+\.\d+", item["upstream_version"]):
            raise RegistryError(f"{path}: {item['id']}: upstream_version is MAJOR.MINOR")
        if not all(isinstance(c, str) and re.fullmatch(r"[0-9a-f]{40}", c) for c in item["upstream_commits"]):
            raise RegistryError(f"{path}: {item['id']}: upstream_commits are full SHA-1s")
        for version, decision in item.get("decisions", {}).items():
            if decision.get("choice") not in CHOICES or not decision.get("why"):
                raise RegistryError(f"{path}: {item['id']}: decision for {version} needs a choice in {CHOICES} "
                                    "and a why (quality, latency, cost)")
    return items


def backport(git, repo, branch, fork_base, commits):
    """[{sha, subject, files, added, removed, clean, conflicts: [(path, reason)]}] for each upstream commit."""
    results = []
    for commit in commits:
        sha, date, subject = git(repo, "show", "--no-patch", "--format=%H%x1f%as%x1f%s", commit).strip().split("\x1f")
        added = removed = files = 0
        for line in git(repo, "show", "--numstat", "--format=", sha).splitlines():
            plus, minus, _ = line.split("\t", 2)
            files += 1
            if plus != "-":
                added, removed = added + int(plus), removed + int(minus)
        out = git(repo, "merge-tree", "--write-tree", "--name-only", "--no-messages", f"--merge-base={sha}^",
                  branch, sha, ok=(0, 1))
        conflicts = [(path, _conflict_reason(git, repo, branch, fork_base, sha, path))
                     for path in out.splitlines()[1:] if path]
        results.append({"sha": sha, "date": date, "subject": subject, "files": files, "added": added,
                        "removed": removed, "clean": not conflicts, "conflicts": conflicts})
    return results


def _conflict_reason(git, repo, branch, fork_base, sha, path):
    present = bool(git(repo, "ls-tree", "--name-only", branch, "--", path).strip())
    ours = bool(git(repo, "diff", "--name-only", fork_base, branch, "--", path).strip())
    earlier = git(repo, "log", "--no-merges", "--format=%h", f"{fork_base}..{sha}^", "--", path).split()
    parts = ["missing here" if not present else "Angelo changed it" if ours else "upstream-only drift"]
    if earlier:
        parts.append(f"{len(earlier)} earlier upstream commit{'s' if len(earlier) != 1 else ''} to it")
    return "; ".join(parts)


def render_backport(results, branch_label):
    out = []
    for result in results:
        out.append(f"{result['sha'][:11]} {result['date']} {result['subject']}")
        out.append(f"  size: {result['files']} files, +{result['added']} -{result['removed']}")
        if result["clean"]:
            out.append(f"  applies to {branch_label} cleanly (text only: build and tests not run)")
        else:
            out.append(f"  conflicts on {branch_label} in {len(result['conflicts'])} files:")
            out += [f"    {path}: {reason}" for path, reason in result["conflicts"]]
    return "\n".join(out) + "\n"


def compare(git, is_ancestor, repo, items, target, target_series, fork_base):
    """Each item against ``target``: [{item, shipped, missing, later, decision}]. ``shipped`` are the backported
    commits ``target`` contains, ``later`` what upstream changed in the item's files after them (or since the fork
    base, for a feature Angelo built itself)."""
    report = []
    for item in items:
        shipped, missing = [], []
        for sha in item["upstream_commits"]:
            (shipped if is_ancestor(repo, sha, target) else missing).append(sha)
        later = []
        if item["paths"]:
            since = shipped[-1] if shipped else fork_base
            later = git(repo, "log", "--no-merges", "--format=%h %as %s", f"{since}..{target}", "--",
                        *item["paths"]).splitlines()
        decision = item.get("decisions", {}).get(target_series)
        report.append({"item": item, "shipped": shipped, "missing": missing, "later": later, "decision": decision})
    return report


def render_compare(report, target_label, target_series):
    if not report:
        return f"No early-adoption items in {REGISTRY.name}.\n"
    out = [f"Early-adoption items against {target_label}:"]
    for entry in report:
        item = entry["item"]
        out.append(f"  {item['id']}: {item['title']} (from {item['upstream_version']}; Angelo {item['angelo']})")
        if item["upstream_commits"]:
            out.append(f"    backported: {len(entry['shipped'])} of {len(item['upstream_commits'])} commits shipped "
                       f"in {target_label}" + (f"; not there: {', '.join(c[:11] for c in entry['missing'])}"
                                                if entry["missing"] else ""))
        else:
            out.append("    built by Angelo; upstream's version is what changed in these files:")
        out += [f"      {line}" for line in entry["later"][:5]]
        if len(entry["later"]) > 5:
            out.append(f"      … {len(entry['later']) - 5} more")
        decision = entry["decision"]
        out.append(f"    decision for {target_series}: " + (f"{decision['choice']} ({decision['why']})" if decision
                                                            else "NONE: record keep ours, take theirs or merge"))
    return "\n".join(out) + "\n"
